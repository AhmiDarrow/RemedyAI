/** Serialize saves per document; independent documents never block each other. */
export function createSerialWriter<T>(write: (key: string, value: T) => Promise<void>) {
  const pending = new Map<string, Promise<void>>()
  return (key: string, value: T): Promise<void> => {
    const previous = pending.get(key) ?? Promise.resolve()
    const next = previous.catch(() => {}).then(() => write(key, value))
    pending.set(key, next)
    void next.finally(() => {
      if (pending.get(key) === next) pending.delete(key)
    }).catch(() => {})
    return next
  }
}
