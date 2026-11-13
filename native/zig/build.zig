const std = @import("std");

/// Windows host primitives (input, GDI capture, DPI, jobs) need these
/// system libraries; other targets export the same symbols as unsupported.
fn linkHostLibraries(module: *std.Build.Module, target: std.Build.ResolvedTarget) void {
    switch (target.result.os.tag) {
        .windows => {
            module.linkSystemLibrary("user32", .{});
            module.linkSystemLibrary("gdi32", .{});
            module.linkSystemLibrary("shcore", .{});
            module.linkSystemLibrary("shell32", .{});
            module.linkSystemLibrary("kernel32", .{});
            module.linkSystemLibrary("ole32", .{});
            module.linkSystemLibrary("oleaut32", .{});
            module.linkSystemLibrary("UIAutomationCore", .{});
        },
        .linux => {
            // nanosleep/clock_gettime + X11/AT-SPI shared objects (PIC required).
            module.link_libc = true;
            module.linkSystemLibrary("X11", .{});
            module.linkSystemLibrary("Xtst", .{});
            module.linkSystemLibrary("atspi", .{});
            module.linkSystemLibrary("dbus-1", .{});
            module.linkSystemLibrary("glib-2.0", .{});
            module.linkSystemLibrary("gobject-2.0", .{});
        },
        .macos => {
            // AX, CGEvent, CGWindowList, Carbon Pasteboard, waitpid/libproc.
            module.link_libc = true;
            // Zig 0.16 only auto-fills framework search paths for a native
            // target (ziglang/zig#25010). `--sysroot` is forwarded to the
            // compiler but those search paths stay "none" when crossing
            // aarch64 → x86_64-macos, so the SDK framework dir has to be
            // added explicitly (ziglang/zig#22704).
            const b = module.owner;
            if (b.sysroot) |sysroot| {
                module.addSystemIncludePath(.{ .cwd_relative = b.pathJoin(&.{ sysroot, "usr/include" }) });
                module.addSystemFrameworkPath(.{ .cwd_relative = b.pathJoin(&.{ sysroot, "System/Library/Frameworks" }) });
            }
            module.linkFramework("ApplicationServices", .{});
            module.linkFramework("CoreGraphics", .{});
            module.linkFramework("CoreFoundation", .{});
        },
        else => {},
    }
}

pub fn build(b: *std.Build) void {
    // Portable shared library: never bake the build host's ISA into remedy_core.
    // GitHub windows runners enable SHA-NI / AVX extras; owners on Comet Lake
    // (e.g. i9-10900K) then die in PolicyHashArgv with STATUS_ILLEGAL_INSTRUCTION
    // (sha256msg1) before :7400 listens — Desktop stuck on "connecting".
    // Override with -Dcpu=native only for local experiments, never for release.
    const target = b.standardTargetOptions(.{
        .default_target = .{
            .cpu_model = .baseline,
        },
    });
    const optimize = b.standardOptimizeOption(.{});
    // X11/AT-SPI are shared objects; static remedy_core must be PIC on Linux.
    const want_pic: ?bool = if (target.result.os.tag == .linux) true else null;
    const module = b.createModule(.{
        .root_source_file = b.path("src/root.zig"),
        .target = target,
        .optimize = optimize,
        .pic = want_pic,
    });
    linkHostLibraries(module, target);
    const static_lib = b.addLibrary(.{
        .name = "remedy_core",
        .linkage = .static,
        .root_module = module,
    });
    b.installArtifact(static_lib);
    const shared_module = b.createModule(.{
        .root_source_file = b.path("src/root.zig"),
        .target = target,
        .optimize = optimize,
        .pic = want_pic,
    });
    linkHostLibraries(shared_module, target);
    const shared_lib = b.addLibrary(.{
        .name = "remedy_core",
        .linkage = .dynamic,
        .root_module = shared_module,
    });
    b.installArtifact(shared_lib);
    const install_header = b.addInstallHeaderFile(b.path("include/remedy_core.h"), "remedy_core.h");
    b.getInstallStep().dependOn(&install_header.step);

    const test_module = b.createModule(.{
        .root_source_file = b.path("src/root.zig"),
        .target = target,
        .optimize = optimize,
        .pic = want_pic,
    });
    linkHostLibraries(test_module, target);
    const tests = b.addTest(.{
        .root_module = test_module,
    });
    const run_tests = b.addRunArtifact(tests);
    const test_step = b.step("test", "Run Remedy Core tests");
    test_step.dependOn(&run_tests.step);
}
