// lossless-json accepts duplicate keys when their values are deeply equal.
// Inspect lexical property identity before parsing can discard that information.
// Callers enforce byte/depth bounds; the JSON parser still owns syntax validation.
export function requireUniqueJsonKeys(text: string): void {
  const containers: (Set<string> | null)[] = [];
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (char === "{") containers.push(new Set());
    else if (char === "[") containers.push(null);
    else if (char === "}" || char === "]") containers.pop();
    else if (char === '"') {
      const start = index;
      for (index += 1; index < text.length; index += 1) {
        if (text[index] === "\\") index += 1;
        else if (text[index] === '"') break;
      }
      let next = index + 1;
      while (next < text.length && /[\x20\t\r\n]/.test(text[next])) next += 1;
      if (text[next] === ":") {
        const keys = containers.at(-1);
        if (!keys) throw new SyntaxError("INVALID_JSON_KEYS");
        const key: string = JSON.parse(text.slice(start, index + 1));
        if (keys.has(key)) throw new SyntaxError("DUPLICATE_JSON_KEY");
        keys.add(key);
      }
    }
  }
}
