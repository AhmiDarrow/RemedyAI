const std = @import("std");
const capability = @import("capability.zig");

const Hmac = std.crypto.auth.hmac.sha2.HmacSha256;
const Sha256 = std.crypto.hash.sha2.Sha256;

pub const token_version: u8 = 1;
pub const token_size = 137;
pub const authenticated_size = token_size - Hmac.mac_length;
pub const max_lifetime_ms: u64 = 15 * 60 * 1000;

pub const Token = struct {
    rights: u64,
    issued_at_ms: u64,
    expires_at_ms: u64,
    nonce: [16]u8,
    subject_hash: [Sha256.digest_length]u8,
    scope_hash: [Sha256.digest_length]u8,
    mac: [Hmac.mac_length]u8,
};

pub const Grant = struct {
    capabilities: capability.Set,
    expires_at_ms: u64,
    nonce: [16]u8,
};

pub fn issue(
    key: []const u8,
    subject: []const u8,
    scope: []const u8,
    rights: capability.Set,
    issued_at_ms: u64,
    expires_at_ms: u64,
    nonce: [16]u8,
) ![token_size]u8 {
    if (key.len < Hmac.key_length or !rights.isValid() or expires_at_ms <= issued_at_ms or
        expires_at_ms - issued_at_ms > max_lifetime_ms)
    {
        return error.InvalidGrant;
    }
    var token = Token{
        .rights = rights.bits,
        .issued_at_ms = issued_at_ms,
        .expires_at_ms = expires_at_ms,
        .nonce = nonce,
        .subject_hash = hash(subject),
        .scope_hash = hash(scope),
        .mac = undefined,
    };
    var encoded: [token_size]u8 = undefined;
    encodeAuthenticated(token, encoded[0..authenticated_size]);
    Hmac.create(&token.mac, encoded[0..authenticated_size], key);
    @memcpy(encoded[authenticated_size..], &token.mac);
    return encoded;
}

pub const Verifier = struct {
    key: []const u8,
    seen: std.AutoHashMap([16]u8, u64),
    mutex: std.atomic.Mutex = .unlocked,

    pub fn init(allocator: std.mem.Allocator, key: []const u8) Verifier {
        return .{ .key = key, .seen = std.AutoHashMap([16]u8, u64).init(allocator) };
    }

    pub fn deinit(self: *Verifier) void {
        self.seen.deinit();
    }

    pub fn verifyAndConsume(
        self: *Verifier,
        encoded: []const u8,
        subject: []const u8,
        scope: []const u8,
        required: capability.Set,
        now_ms: u64,
    ) !Grant {
        if (self.key.len < Hmac.key_length or encoded.len != token_size) return error.InvalidToken;
        const token = try decode(encoded);
        var expected_mac: [Hmac.mac_length]u8 = undefined;
        Hmac.create(&expected_mac, encoded[0..authenticated_size], self.key);
        if (!std.crypto.timing_safe.eql([Hmac.mac_length]u8, expected_mac, token.mac)) {
            return error.InvalidToken;
        }
        if (now_ms < token.issued_at_ms) return error.NotYetValid;
        if (now_ms >= token.expires_at_ms) return error.Expired;
        if (!std.crypto.timing_safe.eql([Sha256.digest_length]u8, hash(subject), token.subject_hash)) {
            return error.SubjectMismatch;
        }
        if (!std.crypto.timing_safe.eql([Sha256.digest_length]u8, hash(scope), token.scope_hash)) {
            return error.ScopeMismatch;
        }
        const rights = capability.Set.fromBits(token.rights);
        if (!rights.isValid() or !rights.containsAll(required)) return error.AccessDenied;
        while (!self.mutex.tryLock()) std.atomic.spinLoopHint();
        defer self.mutex.unlock();
        var entries = self.seen.iterator();
        while (entries.next()) |entry| {
            if (entry.value_ptr.* <= now_ms) _ = self.seen.remove(entry.key_ptr.*);
        }
        if (self.seen.contains(token.nonce)) return error.Replayed;
        try self.seen.put(token.nonce, token.expires_at_ms);
        return .{ .capabilities = rights, .expires_at_ms = token.expires_at_ms, .nonce = token.nonce };
    }
};

fn encodeAuthenticated(token: Token, out: []u8) void {
    out[0] = token_version;
    std.mem.writeInt(u64, out[1..9], token.rights, .little);
    std.mem.writeInt(u64, out[9..17], token.issued_at_ms, .little);
    std.mem.writeInt(u64, out[17..25], token.expires_at_ms, .little);
    @memcpy(out[25..41], &token.nonce);
    @memcpy(out[41..73], &token.subject_hash);
    @memcpy(out[73..105], &token.scope_hash);
}

fn decode(encoded: []const u8) !Token {
    if (encoded.len != token_size or encoded[0] != token_version) return error.InvalidToken;
    var nonce: [16]u8 = undefined;
    var subject_hash: [32]u8 = undefined;
    var scope_hash: [32]u8 = undefined;
    var mac: [32]u8 = undefined;
    @memcpy(&nonce, encoded[25..41]);
    @memcpy(&subject_hash, encoded[41..73]);
    @memcpy(&scope_hash, encoded[73..105]);
    @memcpy(&mac, encoded[105..137]);
    return .{
        .rights = std.mem.readInt(u64, encoded[1..9], .little),
        .issued_at_ms = std.mem.readInt(u64, encoded[9..17], .little),
        .expires_at_ms = std.mem.readInt(u64, encoded[17..25], .little),
        .nonce = nonce,
        .subject_hash = subject_hash,
        .scope_hash = scope_hash,
        .mac = mac,
    };
}

fn hash(value: []const u8) [Sha256.digest_length]u8 {
    var digest: [Sha256.digest_length]u8 = undefined;
    Sha256.hash(value, &digest, .{});
    return digest;
}

test "capability tokens bind rights, subject, scope, lifetime, and nonce" {
    const key = [_]u8{0x5a} ** Hmac.key_length;
    const nonce = [_]u8{0x11} ** 16;
    const encoded = try issue(&key, "agent:7", "workspace:a", capability.Set.one(.filesystem_read), 1000, 2000, nonce);
    var verifier = Verifier.init(std.testing.allocator, &key);
    defer verifier.deinit();
    const grant = try verifier.verifyAndConsume(&encoded, "agent:7", "workspace:a", capability.Set.one(.filesystem_read), 1500);
    try grant.capabilities.require(.filesystem_read);
    try std.testing.expectError(error.Replayed, verifier.verifyAndConsume(&encoded, "agent:7", "workspace:a", capability.Set.one(.filesystem_read), 1500));
}

test "capability token rejects tampering and binding families" {
    const key = [_]u8{0x6b} ** Hmac.key_length;
    const nonce = [_]u8{0x22} ** 16;
    var encoded = try issue(&key, "agent:9", "workspace:b", capability.Set.one(.process_spawn), 1000, 2000, nonce);

    var verifier = Verifier.init(std.testing.allocator, &key);
    defer verifier.deinit();
    try std.testing.expectError(error.NotYetValid, verifier.verifyAndConsume(&encoded, "agent:9", "workspace:b", capability.Set.one(.process_spawn), 999));
    try std.testing.expectError(error.Expired, verifier.verifyAndConsume(&encoded, "agent:9", "workspace:b", capability.Set.one(.process_spawn), 2000));
    try std.testing.expectError(error.SubjectMismatch, verifier.verifyAndConsume(&encoded, "agent:x", "workspace:b", capability.Set.one(.process_spawn), 1500));
    try std.testing.expectError(error.ScopeMismatch, verifier.verifyAndConsume(&encoded, "agent:9", "workspace:x", capability.Set.one(.process_spawn), 1500));
    try std.testing.expectError(error.AccessDenied, verifier.verifyAndConsume(&encoded, "agent:9", "workspace:b", capability.Set.one(.filesystem_read), 1500));
    encoded[8] ^= 1;
    try std.testing.expectError(error.InvalidToken, verifier.verifyAndConsume(&encoded, "agent:9", "workspace:b", capability.Set.one(.process_spawn), 1500));
}

test "issuer rejects invalid lifetime, unknown rights, and short keys" {
    const key = [_]u8{0x2a} ** Hmac.key_length;
    const nonce = [_]u8{0x66} ** 16;
    try std.testing.expectError(
        error.InvalidGrant,
        issue("short", "agent", "scope", capability.Set.one(.filesystem_read), 1, 2, nonce),
    );
    try std.testing.expectError(
        error.InvalidGrant,
        issue(&key, "agent", "scope", capability.Set.fromBits(@as(u64, 1) << 63), 1, 2, nonce),
    );
    try std.testing.expectError(
        error.InvalidGrant,
        issue(&key, "agent", "scope", capability.Set.one(.filesystem_read), 1, max_lifetime_ms + 2, nonce),
    );
}

test "verifier prunes expired consumed nonces" {
    const key = [_]u8{0x3c} ** Hmac.key_length;
    var verifier = Verifier.init(std.testing.allocator, &key);
    defer verifier.deinit();
    const first = try issue(&key, "agent", "scope", capability.Set.one(.filesystem_read), 1000, 2000, [_]u8{0x01} ** 16);
    _ = try verifier.verifyAndConsume(&first, "agent", "scope", capability.Set.one(.filesystem_read), 1500);
    const second = try issue(&key, "agent", "scope", capability.Set.one(.filesystem_read), 2000, 3000, [_]u8{0x02} ** 16);
    _ = try verifier.verifyAndConsume(&second, "agent", "scope", capability.Set.one(.filesystem_read), 2500);
    try std.testing.expectEqual(@as(usize, 1), verifier.seen.count());
}
