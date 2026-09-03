const std = @import("std");

/// Windows host primitives (input, GDI capture, DPI, jobs) need these
/// system libraries; other targets export the same symbols as unsupported.
fn linkHostLibraries(module: *std.Build.Module, target: std.Build.ResolvedTarget) void {
    if (target.result.os.tag != .windows) return;
    module.linkSystemLibrary("user32", .{});
    module.linkSystemLibrary("gdi32", .{});
    module.linkSystemLibrary("shcore", .{});
    module.linkSystemLibrary("kernel32", .{});
}

pub fn build(b: *std.Build) void {
    const target = b.standardTargetOptions(.{});
    const optimize = b.standardOptimizeOption(.{});
    const module = b.createModule(.{
        .root_source_file = b.path("src/root.zig"),
        .target = target,
        .optimize = optimize,
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
    });
    linkHostLibraries(test_module, target);
    const tests = b.addTest(.{
        .root_module = test_module,
    });
    const run_tests = b.addRunArtifact(tests);
    const test_step = b.step("test", "Run Remedy Core tests");
    test_step.dependOn(&run_tests.step);
}
