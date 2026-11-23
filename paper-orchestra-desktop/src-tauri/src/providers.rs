//! Desktop-side mirror of `paper-orchestra/utils/providers.py`: validates
//! providers.json and works out which LLM providers have credentials, using
//! only the values written in the repo's `.env`.

use std::collections::BTreeMap;

use serde_json::{Map, Value};

/// `type` values `_spec_from_dict` accepts (compared after trim + lowercase).
const ACCEPTED_TYPES: &[&str] = &[
    "openai",
    "openai-compatible",
    "openai_compatible",
    "oai",
    "anthropic",
    "claude",
    "gemini",
    "google",
];

/// One provider as the credential check sees it (`ProviderSpec` subset).
#[derive(Debug, Clone)]
struct ProviderCreds {
    name: String,
    kind: &'static str,
    api_key: String,
    api_key_env: Vec<String>,
    base_url_env: Vec<String>,
    key_optional: bool,
    builtin: bool,
    aliases: Vec<String>,
    default_model: String,
    models: Vec<String>,
}

/// A builtin preset, copied from `_builtin_providers()`. The base-URL env var
/// is `<NAME>_BASE_URL` for all of them (GEMINI_BASE_URL for gemini).
struct Builtin {
    name: &'static str,
    kind: &'static str,
    key_envs: &'static [&'static str],
    aliases: &'static [&'static str],
    key_optional: bool,
    default_model: &'static str,
}

const fn b(
    name: &'static str,
    kind: &'static str,
    key_envs: &'static [&'static str],
    aliases: &'static [&'static str],
    key_optional: bool,
    default_model: &'static str,
) -> Builtin {
    Builtin { name, kind, key_envs, aliases, key_optional, default_model }
}

const BUILTINS: &[Builtin] = &[
    b("openai", "openai", &["OPENAI_API_KEY"], &["oai"], false, ""),
    b("anthropic", "anthropic", &["ANTHROPIC_API_KEY"], &["claude"], false, ""),
    b("deepseek", "openai", &["DEEPSEEK_API_KEY"], &[], false, ""),
    b("gemini", "gemini", &["GEMINI_API_KEY"], &["google", "vertex"], false, ""),
    b("qwen", "openai", &["DASHSCOPE_API_KEY", "QWEN_API_KEY"], &["dashscope", "bailian", "aliyun"], false, "qwen-max"),
    b("moonshot", "openai", &["MOONSHOT_API_KEY", "KIMI_API_KEY"], &["kimi"], false, "kimi-k3.1-pro"),
    b("zhipu", "openai", &["ZHIPU_API_KEY", "ZHIPUAI_API_KEY", "GLM_API_KEY"], &["glm", "bigmodel"], false, "glm-4-plus"),
    b("hunyuan", "openai", &["HUNYUAN_API_KEY", "TOKENHUB_API_KEY"], &["hy", "tencent", "tokenhub"], false, "hy4"),
    b("antdigital", "openai", &["ANTDIGITAL_API_KEY", "DTMAAS_API_KEY"], &["ant", "ling", "dtmaas"], false, ""),
    b("longcat", "openai", &["LONGCAT_API_KEY"], &["meituan"], false, ""),
    b("qianfan", "openai", &["QIANFAN_API_KEY", "ERNIE_API_KEY"], &["ernie", "baidu", "wenxin"], false, "ernie-5.1"),
    b("siliconflow", "openai", &["SILICONFLOW_API_KEY"], &["sf"], false, "deepseek-ai/DeepSeek-V3"),
    b("openrouter", "openai", &["OPENROUTER_API_KEY"], &[], false, "anthropic/claude-sonnet-5.5"),
    b("xai", "openai", &["XAI_API_KEY", "GROK_API_KEY"], &["grok"], false, ""),
    b("groq", "openai", &["GROQ_API_KEY"], &[], false, "llama-3.3-70b-versatile"),
    b("mistral", "openai", &["MISTRAL_API_KEY"], &[], false, "mistral-large-latest"),
    b("together", "openai", &["TOGETHER_API_KEY"], &[], false, ""),
    b("ollama", "openai", &["OLLAMA_API_KEY"], &[], true, ""),
    b("lmstudio", "openai", &["LMSTUDIO_API_KEY"], &[], true, ""),
    b("vllm", "openai", &["VLLM_API_KEY"], &[], true, ""),
];

/// Providers `_non_legacy_default` never falls back to.
const LEGACY: &[&str] = &["anthropic", "deepseek", "openai", "gemini"];

/// `_env_prefix`: non-alphanumerics become "_", then upper-case.
pub fn env_prefix(name: &str) -> String {
    name.chars()
        .map(|c| if c.is_ascii_alphanumeric() { c.to_ascii_uppercase() } else { '_' })
        .collect()
}

/// Parses and validates providers.json text the way `_load_config_file` +
/// `_spec_from_dict` would, returning the provider objects. Every file this
/// accepts loads in Python, and every file it rejects makes Python raise.
/// Errors are in Chinese and name the offending entry. Blank text (an empty
/// file) declares no providers.
pub fn parse_providers_json(text: &str) -> Result<Vec<Map<String, Value>>, String> {
    let text = text.strip_prefix('\u{feff}').unwrap_or(text);
    if text.trim().is_empty() {
        return Ok(Vec::new());
    }
    let data: Value = serde_json::from_str(text)
        .map_err(|e| format!("providers.json 不是合法的 JSON：{e}"))?;
    let entries: Vec<Value> = match data {
        Value::Array(items) => items,
        Value::Object(mut obj) => match obj.remove("providers") {
            None | Some(Value::Null) => Vec::new(),
            Some(Value::Array(items)) => items,
            Some(_) => return Err("providers.json 中的 \"providers\" 必须是数组".to_string()),
        },
        _ => {
            return Err(
                "providers.json 顶层必须是 provider 数组，或带 \"providers\" 数组的对象".to_string(),
            )
        }
    };

    let mut out = Vec::with_capacity(entries.len());
    for (i, entry) in entries.into_iter().enumerate() {
        let n = i + 1;
        let Value::Object(obj) = entry else {
            return Err(format!("providers.json 第 {n} 个 provider 必须是对象"));
        };
        let name = match obj.get("name") {
            Some(Value::String(s)) if !s.trim().is_empty() => s.trim().to_string(),
            _ => {
                return Err(format!(
                    "providers.json 第 {n} 个 provider 缺少 name（需为非空字符串）"
                ))
            }
        };
        provider_kind(&obj).map_err(|e| format!("provider \"{name}\" {e}"))?;
        check_field_types(&obj).map_err(|e| format!("provider \"{name}\" {e}"))?;
        out.push(obj);
    }
    Ok(out)
}

/// Field types `_spec_from_dict` accepts (null always counts as absent).
fn check_field_types(obj: &Map<String, Value>) -> Result<(), String> {
    for key in ["base_url", "api_key", "default_model"] {
        if !matches!(obj.get(key), None | Some(Value::Null) | Some(Value::String(_))) {
            return Err(format!("的 {key} 必须是字符串"));
        }
    }
    for key in ["api_key_env", "models", "drop_params", "aliases"] {
        let ok = match obj.get(key) {
            None | Some(Value::Null) | Some(Value::String(_)) => true,
            Some(Value::Array(items)) => items.iter().all(Value::is_string),
            Some(_) => false,
        };
        if !ok {
            return Err(format!("的 {key} 必须是字符串或字符串数组"));
        }
    }
    for key in ["vision", "key_optional"] {
        if !matches!(obj.get(key), None | Some(Value::Null) | Some(Value::Bool(_))) {
            return Err(format!("的 {key} 必须是 true 或 false"));
        }
    }
    match obj.get("headers") {
        None | Some(Value::Null) => {}
        Some(Value::Object(h)) if h.values().all(Value::is_string) => {}
        Some(_) => return Err("的 headers 必须是值为字符串的对象".to_string()),
    }
    Ok(())
}

/// A list-of-strings field (`_str_list`): a single non-blank string is a one-item list.
fn str_list(obj: &Map<String, Value>, key: &str) -> Vec<String> {
    match obj.get(key) {
        Some(Value::String(s)) if !s.trim().is_empty() => vec![s.clone()],
        Some(Value::Array(items)) => items.iter().filter_map(|v| v.as_str().map(str::to_string)).collect(),
        _ => Vec::new(),
    }
}

/// Canonical wire protocol of a provider object (`type`, falling back to
/// `kind`, default "openai"), or the reason it is not accepted.
fn provider_kind(obj: &Map<String, Value>) -> Result<&'static str, String> {
    let (field, raw) = match (obj.get("type"), obj.get("kind")) {
        (Some(v), _) => ("type", v),
        (None, Some(v)) => ("kind", v),
        (None, None) => return Ok("openai"),
    };
    let Value::String(s) = raw else {
        return Err(format!("的 {field} 必须是字符串"));
    };
    let kind = s.trim().to_lowercase();
    match kind.as_str() {
        "openai" | "openai-compatible" | "openai_compatible" | "oai" => Ok("openai"),
        "anthropic" | "claude" => Ok("anthropic"),
        "gemini" | "google" => Ok("gemini"),
        _ => Err(format!(
            "的 {field} \"{}\" 无效，可选值：{}",
            s.trim(),
            ACCEPTED_TYPES.join(" / ")
        )),
    }
}

/// Model routing written at the top level of providers.json, as Python's
/// `config_value()` reads it: `auto_model` and `task_models` ({task: model}).
/// Blank values are dropped; a file that is not a JSON object gives nothing.
pub fn routing_config(text: &str) -> (String, BTreeMap<String, String>) {
    let text = text.strip_prefix('\u{feff}').unwrap_or(text);
    let Ok(Value::Object(obj)) = serde_json::from_str::<Value>(text) else {
        return (String::new(), BTreeMap::new());
    };
    // Only text counts: Python skips falsy values (false, 0, null) and nobody writes
    // a model id as a number or object.
    let as_text = |v: &Value| match v {
        Value::String(s) => s.trim().to_string(),
        _ => String::new(),
    };
    let auto_model = obj.get("auto_model").map(as_text).unwrap_or_default();
    let mut tasks = BTreeMap::new();
    if let Some(Value::Object(map)) = obj.get("task_models") {
        for (task, model) in map {
            let model = as_text(model);
            if !model.is_empty() {
                tasks.insert(task.clone(), model);
            }
        }
    }
    (auto_model, tasks)
}

/// Provider names declared in providers.json, de-duplicated (case-insensitive).
pub fn custom_provider_names(entries: &[Map<String, Value>]) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for obj in entries {
        if let Some(Value::String(name)) = obj.get("name") {
            let name = name.trim().to_string();
            if !out.iter().any(|n| n.eq_ignore_ascii_case(&name)) {
                out.push(name);
            }
        }
    }
    out
}

/// Names of providers with credentials, in the order `list_providers()`
/// yields them. `custom` must come from [`parse_providers_json`].
pub fn configured_providers(
    env: &BTreeMap<String, String>,
    custom: &[Map<String, Value>],
) -> Vec<String> {
    configured_specs(env, custom).into_iter().map(|s| s.name).collect()
}

/// `llm_backend_utils._non_legacy_default()`: the "provider:model" that
/// `auto` falls back to when no legacy key is set, or "" when there is none
/// (Python then picks its own fallback, which needs a Gemini key).
pub fn auto_fallback_model(env: &BTreeMap<String, String>, custom: &[Map<String, Value>]) -> String {
    let mut specs = configured_specs(env, custom);
    specs.sort_by(|a, b| (a.builtin, &a.name).cmp(&(b.builtin, &b.name)));
    for spec in specs {
        if LEGACY.contains(&spec.name.as_str()) {
            continue;
        }
        let from_env = env
            .get(&format!("{}_DEFAULT_MODEL", env_prefix(&spec.name)))
            .map(|v| v.trim().to_string())
            .unwrap_or_default();
        let model = if !from_env.is_empty() {
            from_env
        } else if !spec.default_model.is_empty() {
            spec.default_model.clone()
        } else {
            spec.models.first().cloned().unwrap_or_default()
        };
        if !model.is_empty() {
            return format!("{}:{model}", spec.name);
        }
    }
    String::new()
}

fn configured_specs(env: &BTreeMap<String, String>, custom: &[Map<String, Value>]) -> Vec<ProviderCreds> {
    let mut specs: Vec<ProviderCreds> = BUILTINS
        .iter()
        .map(|b| ProviderCreds {
            name: b.name.to_string(),
            kind: b.kind,
            api_key: String::new(),
            api_key_env: b.key_envs.iter().map(|k| k.to_string()).collect(),
            base_url_env: vec![format!("{}_BASE_URL", env_prefix(b.name))],
            key_optional: b.key_optional,
            builtin: true,
            aliases: b.aliases.iter().map(|a| a.to_string()).collect(),
            default_model: b.default_model.to_string(),
            models: Vec::new(),
        })
        .collect();

    // Ordered name/alias -> spec index, replicating the Python dict:
    // builtin aliases use setdefault, custom names and aliases overwrite in
    // place (keeping the key's position), new keys append.
    let mut lookup: Vec<(String, usize)> = Vec::new();
    fn put(lookup: &mut Vec<(String, usize)>, key: String, idx: usize, overwrite: bool) {
        match lookup.iter_mut().find(|(k, _)| *k == key) {
            Some(slot) if overwrite => slot.1 = idx,
            Some(_) => {}
            None => lookup.push((key, idx)),
        }
    }
    for (idx, spec) in specs.iter().enumerate() {
        put(&mut lookup, spec.name.to_lowercase(), idx, true);
        for alias in &spec.aliases {
            put(&mut lookup, alias.to_lowercase(), idx, false);
        }
    }
    for obj in custom {
        let Some(spec) = custom_spec(obj) else { continue };
        let idx = specs.len();
        let keys: Vec<String> = std::iter::once(spec.name.to_lowercase())
            .chain(spec.aliases.iter().map(|a| a.to_lowercase()))
            .collect();
        specs.push(spec);
        for key in keys {
            put(&mut lookup, key, idx, true);
        }
    }

    let mut seen_idx: Vec<usize> = Vec::new();
    let mut out: Vec<ProviderCreds> = Vec::new();
    for (_, idx) in &lookup {
        if seen_idx.contains(idx) {
            continue;
        }
        seen_idx.push(*idx);
        let spec = &specs[*idx];
        if has_credentials(spec, env) && !out.iter().any(|n| n.name.eq_ignore_ascii_case(&spec.name)) {
            out.push(spec.clone());
        }
    }
    out
}

fn custom_spec(obj: &Map<String, Value>) -> Option<ProviderCreds> {
    let name = obj.get("name")?.as_str()?.trim().to_string();
    if name.is_empty() {
        return None;
    }
    let kind = provider_kind(obj).ok()?;
    let prefix = env_prefix(&name);
    let mut api_key_env = str_list(obj, "api_key_env");
    api_key_env.push(format!("{prefix}_API_KEY"));
    let text = |key: &str| obj.get(key).and_then(Value::as_str).unwrap_or_default().to_string();
    Some(ProviderCreds {
        name,
        kind,
        api_key: text("api_key"),
        api_key_env,
        base_url_env: vec![format!("{prefix}_BASE_URL")],
        key_optional: obj.get("key_optional").and_then(Value::as_bool).unwrap_or(false),
        builtin: false,
        aliases: str_list(obj, "aliases"),
        default_model: text("default_model"),
        models: str_list(obj, "models"),
    })
}

/// `ProviderSpec.has_credentials`, reading variables from `.env` only.
fn has_credentials(spec: &ProviderCreds, env: &BTreeMap<String, String>) -> bool {
    let set = |key: &str| env.get(key).is_some_and(|v| !v.trim().is_empty());
    if spec.kind == "gemini" && spec.name == "gemini" && set("VERTEX_AI_PROJECT") && set("VERTEX_AI_LOCATION")
    {
        return true;
    }
    // An inline key that expands to nothing, or still names an unset
    // variable, does not count (`resolved_api_key` falls back to api_key_env).
    if !spec.api_key.is_empty() {
        let key = expand_vars(&spec.api_key, env);
        if !key.trim().is_empty() && !has_unexpanded_var(&key) {
            return true;
        }
    }
    if spec.api_key_env.iter().any(|k| set(k)) {
        return true;
    }
    if spec.key_optional {
        // Local endpoints count only once their URL is set explicitly.
        return spec.base_url_env.iter().any(|k| set(k)) || !spec.builtin;
    }
    false
}

/// `_UNEXPANDED_VAR` in providers.py: a `$NAME` / `${...}` that expandvars left in place.
fn has_unexpanded_var(value: &str) -> bool {
    value.match_indices('$').any(|(i, _)| {
        let rest = &value[i + 1..];
        match rest.chars().next() {
            Some('{') => rest.contains('}'),
            Some(c) => c.is_alphanumeric() || c == '_',
            None => false,
        }
    })
}

/// `os.path.expandvars` (POSIX flavour) against the `.env` values: `$NAME`
/// and `${NAME}` are replaced when defined, otherwise left untouched.
fn expand_vars(value: &str, env: &BTreeMap<String, String>) -> String {
    let mut out = String::with_capacity(value.len());
    let mut rest = value;
    while let Some(pos) = rest.find('$') {
        out.push_str(&rest[..pos]);
        let after = &rest[pos + 1..];
        let (name, consumed) = if let Some(braced) = after.strip_prefix('{') {
            match braced.find('}') {
                Some(end) => (&braced[..end], end + 2),
                None => ("", 0),
            }
        } else {
            let end = after
                .find(|c: char| !(c.is_ascii_alphanumeric() || c == '_'))
                .unwrap_or(after.len());
            (&after[..end], end)
        };
        match env.get(name) {
            Some(val) if !name.is_empty() => {
                out.push_str(val);
                rest = &after[consumed..];
            }
            _ => {
                out.push('$');
                rest = after;
            }
        }
    }
    out.push_str(rest);
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn env(pairs: &[(&str, &str)]) -> BTreeMap<String, String> {
        pairs.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect()
    }

    #[test]
    fn providers_json_valid_object_and_array() {
        let obj = r#"{
            "auto_model": "myproxy:gpt-4o",
            "providers": [
                {"name": "myproxy", "type": "openai-compatible", "base_url": "https://x/v1"},
                {"name": "claude-relay", "type": "Claude"},
                {"name": "g", "kind": "google"},
                {"name": "plain"}
            ]
        }"#;
        let parsed = parse_providers_json(obj).unwrap();
        assert_eq!(custom_provider_names(&parsed), ["myproxy", "claude-relay", "g", "plain"]);

        let arr = r#"[{"name": "local", "type": "OAI", "key_optional": true}]"#;
        assert_eq!(parse_providers_json(arr).unwrap().len(), 1);

        assert!(parse_providers_json("[]").unwrap().is_empty());
        assert!(parse_providers_json(r#"{"auto_model": "qwen:qwen-max"}"#).unwrap().is_empty());
        assert!(parse_providers_json(r#"{"providers": null}"#).unwrap().is_empty());
    }

    #[test]
    fn providers_json_rejects_missing_name() {
        let err = parse_providers_json(r#"[{"name": "ok"}, {"type": "openai"}]"#).unwrap_err();
        assert!(err.contains("第 2 个") && err.contains("name"), "{err}");
        let err = parse_providers_json(r#"[{"name": "   "}]"#).unwrap_err();
        assert!(err.contains("name"), "{err}");
        let err = parse_providers_json(r#"[{"name": 42}]"#).unwrap_err();
        assert!(err.contains("name"), "{err}");
    }

    #[test]
    fn providers_json_rejects_bad_type() {
        let err = parse_providers_json(r#"[{"name": "x", "type": "cohere"}]"#).unwrap_err();
        assert!(err.contains("\"x\"") && err.contains("cohere"), "{err}");
        let err = parse_providers_json(r#"[{"name": "x", "type": 3}]"#).unwrap_err();
        assert!(err.contains("字符串"), "{err}");
    }

    #[test]
    fn providers_json_rejects_bad_shape_and_json() {
        let err = parse_providers_json(r#"{"providers": [ {"name": "a"}, ]}"#).unwrap_err();
        assert!(err.contains("不是合法的 JSON"), "{err}");
        let err = parse_providers_json("\"just a string\"").unwrap_err();
        assert!(err.contains("顶层"), "{err}");
        let err = parse_providers_json(r#"{"providers": {"name": "a"}}"#).unwrap_err();
        assert!(err.contains("必须是数组"), "{err}");
        let err = parse_providers_json(r#"["qwen"]"#).unwrap_err();
        assert!(err.contains("必须是对象"), "{err}");
    }

    #[test]
    fn detects_builtin_providers_from_env() {
        let e = env(&[
            ("OPENAI_API_KEY", "sk-x"),
            ("ANTHROPIC_API_KEY", "   "),
            ("KIMI_API_KEY", "k"),
            ("TOKENHUB_API_KEY", "t"),
            ("QIANFAN_API_KEY", "q"),
            ("ARK_API_KEY", "a"),     // Doubao: no longer a builtin (2026-11-19)
            ("MINIMAX_API_KEY", "m"), // MiniMax: likewise
            ("OLLAMA_API_KEY", ""),
            ("LMSTUDIO_BASE_URL", "http://127.0.0.1:1234/v1"),
        ]);
        assert_eq!(configured_providers(&e, &[]), ["openai", "moonshot", "hunyuan", "qianfan", "lmstudio"]);

        // ollama / vllm without a base URL do not count; gemini via Vertex does.
        let e = env(&[("VERTEX_AI_PROJECT", "p"), ("VERTEX_AI_LOCATION", "us-central1")]);
        assert_eq!(configured_providers(&e, &[]), ["gemini"]);
        let e = env(&[("VERTEX_AI_PROJECT", "p")]);
        assert!(configured_providers(&e, &[]).is_empty());
        // GOOGLE_API_KEY is not a Gemini key (providers.py reads GEMINI_API_KEY only).
        let e = env(&[("GOOGLE_API_KEY", "g"), ("GLM_API_KEY", "z")]);
        assert_eq!(configured_providers(&e, &[]), ["zhipu"]);
    }

    #[test]
    fn detects_custom_providers() {
        let custom = parse_providers_json(
            r#"[
                {"name": "inline", "api_key": "sk-inline"},
                {"name": "by-env", "api_key_env": "RELAY_TOKEN"},
                {"name": "by-env-list", "api_key_env": ["A_TOKEN", "B_TOKEN"]},
                {"name": "my.proxy"},
                {"name": "local-box", "key_optional": true},
                {"name": "unset", "api_key": "${EMPTY_VAR}"},
                {"name": "nothing"}
            ]"#,
        )
        .unwrap();
        let e = env(&[
            ("RELAY_TOKEN", "t"),
            ("B_TOKEN", "b"),
            ("MY_PROXY_API_KEY", "p"),
            ("EMPTY_VAR", ""),
        ]);
        assert_eq!(
            configured_providers(&e, &custom),
            ["inline", "by-env", "by-env-list", "my.proxy", "local-box"]
        );
        assert_eq!(custom_provider_names(&custom).len(), 7);
    }

    /// Expected value produced by running utils/providers.py
    /// `configured_providers()` on the same file and variables.
    #[test]
    fn matches_python_registry_order() {
        let custom = parse_providers_json(
            r#"[
                {"name": "inline", "api_key": "sk-inline"},
                {"name": "by-env", "api_key_env": "RELAY_TOKEN"},
                {"name": "by-env-list", "api_key_env": ["A_TOKEN", "B_TOKEN"]},
                {"name": "my.proxy"},
                {"name": "local-box", "key_optional": true},
                {"name": "unset", "api_key": "${EMPTY_VAR}"},
                {"name": "nothing"},
                {"name": "deepseek"},
                {"name": "Relay2", "type": "Claude", "aliases": ["oai"]}
            ]"#,
        )
        .unwrap();
        let e = env(&[
            ("RELAY_TOKEN", "t"),
            ("B_TOKEN", "b"),
            ("MY_PROXY_API_KEY", "p"),
            ("EMPTY_VAR", ""),
            ("OPENAI_API_KEY", "sk"),
            ("KIMI_API_KEY", "k"),
            ("LMSTUDIO_BASE_URL", "http://x"),
            ("VERTEX_AI_PROJECT", "p"),
            ("VERTEX_AI_LOCATION", "l"),
            ("RELAY2_API_KEY", "r"),
        ]);
        assert_eq!(
            configured_providers(&e, &custom),
            [
                "openai", "Relay2", "gemini", "moonshot", "lmstudio", "inline", "by-env",
                "by-env-list", "my.proxy", "local-box",
            ]
        );
        assert_eq!(
            custom_provider_names(&custom),
            [
                "inline", "by-env", "by-env-list", "my.proxy", "local-box", "unset", "nothing",
                "deepseek", "Relay2",
            ]
        );
    }

    #[test]
    fn custom_provider_overrides_builtin_of_same_name() {
        // A custom "deepseek" with its own key replaces the builtin entry.
        let custom = parse_providers_json(r#"[{"name": "deepseek", "api_key": "sk-ds"}]"#).unwrap();
        assert_eq!(configured_providers(&env(&[]), &custom), ["deepseek"]);
        let custom = parse_providers_json(r#"[{"name": "deepseek"}]"#).unwrap();
        assert!(configured_providers(&env(&[]), &custom).is_empty());
        assert_eq!(configured_providers(&env(&[("DEEPSEEK_API_KEY", "k")]), &custom), ["deepseek"]);
    }

    /// Each of these makes `_get_registry` raise in Python (checked against
    /// utils/providers.py), so the desktop app must reject them too.
    #[test]
    fn providers_json_rejects_field_types_python_cannot_load() {
        for (bad, needle) in [
            (r#"[{"name": "x", "headers": ["a"]}]"#, "headers"),
            (r#"[{"name": "x", "headers": {"A": 1}}]"#, "headers"),
            (r#"[{"name": "x", "api_key_env": 5}]"#, "api_key_env"),
            (r#"[{"name": "x", "api_key_env": [5]}]"#, "api_key_env"),
            (r#"[{"name": "x", "models": 5}]"#, "models"),
            (r#"[{"name": "x", "drop_params": {"a": 1}}]"#, "drop_params"),
            (r#"[{"name": "x", "aliases": [1]}]"#, "aliases"),
            (r#"[{"name": "x", "vision": "false"}]"#, "vision"),
            (r#"[{"name": "x", "key_optional": 1}]"#, "key_optional"),
            (r#"[{"name": "x", "api_key": 123}]"#, "api_key"),
            (r#"{"providers": {}}"#, "必须是数组"),
        ] {
            let err = parse_providers_json(bad).unwrap_err();
            assert!(err.contains(needle), "{bad}: {err}");
        }
        // Accepted shapes: nulls, single strings for list fields, BOM, blank text.
        let ok = parse_providers_json(
            "\u{feff}[{\"name\": \"x\", \"aliases\": \"rl\", \"models\": null, \"vision\": null, \"headers\": {\"X-A\": \"1\"}}]",
        )
        .unwrap();
        assert_eq!(ok.len(), 1);
        assert!(parse_providers_json("").unwrap().is_empty());
        assert!(parse_providers_json(" \n ").unwrap().is_empty());
        // A one-string alias is one alias ("rl"), not two ("r", "l").
        let custom = parse_providers_json(r#"[{"name": "x", "aliases": "rl", "api_key": "k"}]"#).unwrap();
        assert_eq!(custom_spec(&custom[0]).unwrap().aliases, ["rl"]);
    }

    #[test]
    fn inline_key_with_unset_variable_falls_back_like_python() {
        let custom = parse_providers_json(r#"[{"name": "relay", "api_key": "${RELAY_TOKEN}"}]"#).unwrap();
        // RELAY_TOKEN empty: Python's resolved_api_key() falls back to RELAY_API_KEY.
        let e = env(&[("RELAY_TOKEN", ""), ("RELAY_API_KEY", "sk-r")]);
        assert_eq!(configured_providers(&e, &custom), ["relay"]);
        // Unset (left as "${RELAY_TOKEN}") and no fallback: not configured.
        assert!(configured_providers(&env(&[]), &custom).is_empty());
        assert_eq!(configured_providers(&env(&[("RELAY_TOKEN", "t")]), &custom), ["relay"]);
        assert!(has_unexpanded_var("a${B}") && has_unexpanded_var("$B") && !has_unexpanded_var("a$ b$"));
    }

    #[test]
    fn auto_fallback_matches_non_legacy_default() {
        // Legacy keys never count; presets with a builtin default are used.
        let e = env(&[("OPENAI_API_KEY", "sk"), ("DASHSCOPE_API_KEY", "q")]);
        assert_eq!(auto_fallback_model(&e, &[]), "qwen:qwen-max");
        // Sorted by name: moonshot (with an env default) before qwen.
        let e = env(&[("DASHSCOPE_API_KEY", "q"), ("KIMI_API_KEY", "k"), ("MOONSHOT_DEFAULT_MODEL", "kimi-k2")]);
        assert_eq!(auto_fallback_model(&e, &[]), "moonshot:kimi-k2");
        // A preset without a default is skipped.
        let e = env(&[("XAI_API_KEY", "x"), ("ZHIPU_API_KEY", "z")]);
        assert_eq!(auto_fallback_model(&e, &[]), "zhipu:glm-4-plus");
        assert_eq!(auto_fallback_model(&env(&[("XAI_API_KEY", "x")]), &[]), "");
        assert_eq!(auto_fallback_model(&env(&[("ARK_API_KEY", "a")]), &[]), "");
        assert_eq!(auto_fallback_model(&env(&[("HUNYUAN_API_KEY", "h")]), &[]), "hunyuan:hy4");
        assert_eq!(auto_fallback_model(&env(&[("QIANFAN_API_KEY", "q")]), &[]), "qianfan:ernie-5.1");
        // Presets without a default (Ant Digital, LongCat) stay out of auto.
        let e = env(&[("DASHSCOPE_API_KEY", "q"), ("ANTDIGITAL_API_KEY", "a"), ("LONGCAT_API_KEY", "l")]);
        assert_eq!(auto_fallback_model(&e, &[]), "qwen:qwen-max");
        // Custom providers come first: default_model, then models[0].
        let custom = parse_providers_json(
            r#"[{"name": "zz", "api_key": "k", "models": ["m1", "m2"]}, {"name": "aa", "api_key": "k"}]"#,
        )
        .unwrap();
        assert_eq!(auto_fallback_model(&env(&[("DASHSCOPE_API_KEY", "q")]), &custom), "zz:m1");
    }

    #[test]
    fn expand_vars_matches_posix_expandvars() {
        let e = env(&[("A", "1"), ("EMPTY", "")]);
        assert_eq!(expand_vars("$A-${A}", &e), "1-1");
        assert_eq!(expand_vars("${EMPTY}", &e), "");
        assert_eq!(expand_vars("$MISSING/${MISSING}", &e), "$MISSING/${MISSING}");
        assert_eq!(expand_vars("cost $ 5 ${unterminated", &e), "cost $ 5 ${unterminated");
    }

    #[test]
    fn env_prefix_matches_python() {
        assert_eq!(env_prefix("my-proxy.v2"), "MY_PROXY_V2");
        assert_eq!(env_prefix("lmstudio"), "LMSTUDIO");
    }

    #[test]
    fn routing_config_reads_top_level_auto_and_task_models() {
        let (auto, tasks) = routing_config(
            r#"{"providers": [], "auto_model": " qwen:qwen-max ", "task_models": {"outline": "claude-opus-5-5", "plot_plan": "  ", "format_fix": null, "peer_review": false}}"#,
        );
        assert_eq!(auto, "qwen:qwen-max");
        assert_eq!(tasks.len(), 1);
        assert_eq!(tasks["outline"], "claude-opus-5-5");
        assert_eq!(routing_config("[]"), (String::new(), BTreeMap::new()));
        assert_eq!(routing_config("not json"), (String::new(), BTreeMap::new()));
        assert_eq!(routing_config(""), (String::new(), BTreeMap::new()));
    }
}
