export function devLogError(...args: unknown[]): void {
  if (process.env.NODE_ENV !== "development") return;
  // eslint-disable-next-line no-console
  console.error(...args);
}

