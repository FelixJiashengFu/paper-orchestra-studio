// Masking of literal credentials inside providers.json text, for the settings editor.

/**
 * Field names whose string value is a credential when written literally into
 * providers.json: `api_key` itself, plus auth-like entries under `headers`.
 * The closing quote right after the name keeps `api_key_env` (a variable
 * *name*, not a secret) out of the match. The value's closing quote is
 * optional so an unterminated, half-written value is masked too.
 */
const JSON_SECRET_FIELD =
  /("(?:api_key|api-key|apikey|x-api-key|authorization|proxy-authorization|token|access_token|api_token|secret|client_secret)"\s*:\s*")((?:[^"\\\n]|\\.)*)("?)/gi;

/** Masks a literal secret, keeping `${VAR}` / `$VAR` references and a `Bearer ` prefix readable. */
function maskSecretValue(value: string): string {
  const literal = value.replace(/\$\{[^}]*\}|\$\w+/g, "").replace(/^\s*(Bearer|Basic|Token)\s+/i, "").trim();
  if (!literal) return value;
  const prefix = /^\s*(Bearer|Basic|Token)\s+/i.exec(value)?.[0] ?? "";
  return `${prefix}••••••••（已隐藏）`;
}

/**
 * providers.json text with every literal credential masked. Works on invalid
 * JSON too (regex-based), so a half-typed file never leaks its key either.
 */
export function maskProvidersJsonSecrets(text: string): string {
  return text.replace(JSON_SECRET_FIELD, (m, pre: string, val: string, post: string) => {
    const masked = maskSecretValue(val);
    return masked === val ? m : `${pre}${masked}${post}`;
  });
}
