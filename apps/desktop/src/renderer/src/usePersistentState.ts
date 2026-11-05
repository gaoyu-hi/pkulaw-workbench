import {
  useEffect,
  useState,
  type Dispatch,
  type SetStateAction
} from "react";

export function readPersistentValue<T>(key: string, fallback: T): T {
  try {
    const raw = globalThis.localStorage?.getItem(key);
    if (raw === null || raw === undefined) return fallback;
    const parsed = JSON.parse(raw) as T | null;
    return parsed === null ? fallback : parsed;
  } catch {
    return fallback;
  }
}

export function writePersistentValue<T>(key: string, value: T): void {
  try {
    globalThis.localStorage?.setItem(key, JSON.stringify(value));
  } catch {
    // Persistence is a convenience; collection must remain usable if it fails.
  }
}

export function usePersistentState<T>(
  key: string,
  initialValue: T | (() => T),
  normalizeStored?: (value: unknown) => T
): [T, Dispatch<SetStateAction<T>>] {
  const [value, setValue] = useState<T>(() => {
    const fallback =
      typeof initialValue === "function"
        ? (initialValue as () => T)()
        : initialValue;
    const stored = readPersistentValue<unknown>(key, fallback);
    return normalizeStored ? normalizeStored(stored) : stored as T;
  });

  useEffect(() => {
    writePersistentValue(key, value);
  }, [key, value]);

  return [value, setValue];
}
