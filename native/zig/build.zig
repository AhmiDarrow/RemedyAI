const std = @import("std");

pub fn build(b: *std.Build) void {
    const target = b.standardTargetOptions(.{});
    const optimize = b.standardOptimizeOption(.{});
    const module = b.createModule(.{
        .root_source_file = b.path("src/root.zig"),
        .target = target,
        .optimize = optimize,
    });
    const static_lib = b.addLibrary(.{
        .name = "remedy_core",
        .linkage = .static,
        .root_module = module,
    });
    b.installArtifact(static_lib);
    const shared_lib = b.addLibrary(.{
        .name = "remedy_core",
        .linkage = .dynamic,
        .root_module = b.createModule(.{
            .root_source_file = b.path("src/root.zig"),
            .target = target,
            .optimize = optimize,
        }),
    });
    b.installArtifact(shared_lib);
    const install_header = b.addInstallHeaderFile(b.path("include/remedy_core.h"), "remedy_core.h");
    b.getInstallStep().dependOn(&install_header.step);

    const tests = b.addTest(.{
        .root_module = b.createModule(.{
            .root_source_file = b.path("src/root.zig"),
            .target = target,
            .optimize = optimize,
        }),
    });
    const run_tests = b.addRunArtifact(tests);
    const test_step = b.step("test", "Run Remedy Core tests");
    test_step.dependOn(&run_tests.step);
}
