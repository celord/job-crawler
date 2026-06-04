export type SkillTier = "intern" | "entry" | "mid" | "senior";

export function classifyTier(title: string | null): SkillTier {
  if (!title) return "mid";
  const t = title.toLowerCase();

  let score = 0;

  // Strong senior signals
  if (/\b(chief|cto|ceo|coo|cpo|ciso|vp|vice\s+president|director)\b/.test(t)) score += 50;
  if (/\b(principal|distinguished|fellow)\b/.test(t)) score += 40;
  if (/\b(staff|lead|head\s+of)\b/.test(t)) score += 30;
  if (/\b(senior|sr\.?)\b/.test(t)) score += 20;
  if (/\b(architect|manager)\b/.test(t)) score += 15;
  // Level numbers: L4-L9 or Level 4-9
  if (/\bl(?:evel\s*)?[4-9]\b/.test(t)) score += 15;
  // Roman numerals IV, V = senior; III = mild senior lean
  if (/\b(iv|v)\b/.test(t)) score += 15;
  if (/\biii\b/.test(t)) score += 10;

  // Junior signals
  if (/\b(junior|jr\.?)\b/.test(t)) score -= 20;
  if (/\b(trainee|graduate|new\s*grad|entry[\s-]?level|associate)\b/.test(t)) score -= 25;
  if (/\b(intern(ship)?)\b/.test(t)) score -= 100;
  // Roman numeral I, II = entry lean
  if (/\bii\b/.test(t)) score -= 5;
  if (/(?<!\b[a-z])\bi\b(?!\b[a-z])/.test(t)) score -= 10;

  if (score <= -50) return "intern";
  if (score <= -5)  return "entry";
  if (score >= 15)  return "senior";
  return "mid";
}

export function asString(value: unknown): string | null {
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed.length > 0 ? trimmed : null;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return null;
}

export function firstString(...values: unknown[]): string | null {
  for (const value of values) {
    const stringValue = asString(value);
    if (stringValue !== null) {
      return stringValue;
    }
  }
  return null;
}

export function joinStrings(values: unknown, separator = ", "): string | null {
  if (!Array.isArray(values)) {
    return asString(values);
  }

  const strings = values.map(asString).filter((value): value is string => value !== null);
  return strings.length > 0 ? strings.join(separator) : null;
}

export function compactObjectStrings(values: unknown): string | null {
  if (!values || typeof values !== "object") {
    return asString(values);
  }

  const strings = Object.values(values as Record<string, unknown>)
    .map(asString)
    .filter((value): value is string => value !== null);

  return strings.length > 0 ? strings.join(", ") : null;
}
