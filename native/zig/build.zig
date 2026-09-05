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
        else => {},
    }
}

pub fn build(b: *std.Build) void {
    const target = b.standardTargetOptions(.{});
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
