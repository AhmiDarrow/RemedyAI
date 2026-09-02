#ifndef REMEDY_CORE_H
#define REMEDY_CORE_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define REMEDY_CORE_ABI_VERSION 1u

enum remedy_core_status {
    REMEDY_CORE_OK = 0,
    REMEDY_CORE_INVALID_ARGUMENT = 1,
    REMEDY_CORE_ACCESS_DENIED = 2,
    REMEDY_CORE_OPERATION_FAILED = 3,
};

enum remedy_core_capability {
    REMEDY_CORE_FILESYSTEM_READ = UINT64_C(1) << 0,
    REMEDY_CORE_FILESYSTEM_WRITE = UINT64_C(1) << 1,
    REMEDY_CORE_PROCESS_SPAWN = UINT64_C(1) << 2,
    REMEDY_CORE_SYSTEM_READ = UINT64_C(1) << 3,
};

uint32_t remedy_core_abi_version(void);
uint8_t remedy_core_validate_frame(const uint8_t *ptr, size_t len);

int32_t remedy_core_file_size(
    uint64_t capability_bits,
    const uint8_t *root_ptr,
    size_t root_len,
    const uint8_t *path_ptr,
    size_t path_len,
    uint64_t *out_size
);

int32_t remedy_core_logical_cpu_count(
    uint64_t capability_bits,
    size_t *out_count
);

#ifdef __cplusplus
}
#endif

#endif
