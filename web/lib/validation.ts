// Form rules for the signup form, checked in the browser while people type.
//
// These mirror api/services/validation.py — the server checks everything
// again, so these exist only to catch mistakes early with a clear message.
//
//   Names          letters from any alphabet (accents fine), with single
//                  spaces, hyphens, apostrophes (' ’) or periods; must start
//                  with a capital letter (capitalizeFirst() fixes that as you type)
//   Business name  2–200 characters: letters, numbers, spaces, & ' ’ . , - ( ) / + ! # @ : ;
//                  starting with a letter or number
//   Email          one @, a domain with a dot (example.com), no spaces

/** "ana" -> "Ana" while typing (only the very first letter is changed). */
export function capitalizeFirst(value: string): string {
  return value.replace(/^\s*(\p{Ll})/u, (m) => m.toUpperCase());
}

const tidy = (v: string) => v.normalize("NFC").replace(/\s+/g, " ").trim();

/** An error message for a first or last name, or null when it's fine. */
export function personNameError(value: string, field: string): string | null {
  const name = tidy(value);
  if (!name) return `${field} is required.`;
  if (name.length > 100) return `${field} must be 100 characters or fewer.`;
  const bad = [...new Set([...name].filter((ch) => !/[\p{L}\p{M} \-'’.]/u.test(ch)))];
  if (bad.length) return `${field} can only use letters (accents are fine), spaces, hyphens, apostrophes and periods — remove: ${bad.join(" ")}`;
  const first = name[0];
  if (!/\p{L}/u.test(first)) return `${field} must start with a letter.`;
  if (first.toUpperCase() !== first.toLowerCase() && first !== first.toUpperCase())
    return `${field} must start with a capital letter (for example, Ana — not ana).`;
  if (/[ \-'’.]{2,}/.test(name.replace(/\. /g, "."))) return `${field} has two separators in a row (like -- or '').`;
  if (/[ \-'’]$/.test(name)) return `${field} can't end with a hyphen or apostrophe.`;
  return null;
}

/** An error message for a business name, or null when it's fine. */
export function companyNameError(value: string): string | null {
  const name = tidy(value);
  if (name.length < 2) return "Business name must be at least 2 characters.";
  if (name.length > 200) return "Business name must be 200 characters or fewer.";
  const bad = [...new Set([...name].filter((ch) => !/[\p{L}\p{M}\p{Nd}&'’.,\-()/+!#@:; ]/u.test(ch)))];
  if (bad.length) return `Business name can use letters, numbers, spaces and & ' . , - ( ) / + ! # @ : ; — remove: ${bad.join(" ")}`;
  if (!/[\p{L}\p{Nd}]/u.test(name[0])) return "Business name must start with a letter or a number.";
  return null;
}

/** An error message for an email address, or null when it looks right. */
export function emailError(value: string): string | null {
  const email = value.trim();
  if (!email) return "Email address is required.";
  // Something@something.something, no spaces, one @, no dots at the ends of the domain.
  if (!/^[^\s@]+@[^\s@.]+(\.[^\s@.]+)+$/.test(email)) return "Enter a valid email address, like name@example.com.";
  return null;
}

/** Emails used more than once in the same form (compared in lowercase). */
export function repeatedEmails(emails: string[]): string[] {
  const seen = new Map<string, number>();
  emails.map((e) => e.trim().toLowerCase()).filter(Boolean).forEach((e) => seen.set(e, (seen.get(e) ?? 0) + 1));
  return [...seen].filter(([, n]) => n > 1).map(([e]) => e);
}
