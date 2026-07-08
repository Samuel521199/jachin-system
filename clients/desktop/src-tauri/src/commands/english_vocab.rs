use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::fs;
use std::fs::OpenOptions;
use std::io::Write;
use std::path::PathBuf;
use std::process::{Child, Command, Output, Stdio};
use std::sync::{Mutex, OnceLock};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;

#[derive(Debug, Clone, Deserialize)]
pub struct EnglishVocabLookupInput {
    pub word: String,
    pub book_id: Option<String>,
    pub context_sentence: Option<String>,
    pub require_final_example: Option<bool>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EnglishVocabLookupResult {
    pub word: String,
    pub phonetic: String,
    pub part_of_speech: String,
    pub meaning_cn: String,
    pub example: String,
    pub example_cn: String,
    pub source: String,
    pub model: String,
    pub refresh_hint: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EnglishVocabProgress {
    pub seen: u32,
    pub known: u32,
    pub fuzzy: u32,
    pub unknown: u32,
    pub status: String,
    pub last_seen_at: Option<u64>,
    pub due_at: Option<u64>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct EnglishVocabDailyStats {
    pub total: u32,
    pub known: u32,
    pub fuzzy: u32,
    pub unknown: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EnglishVocabState {
    pub selected_book_id: String,
    pub progress: BTreeMap<String, EnglishVocabProgress>,
    pub daily: BTreeMap<String, EnglishVocabDailyStats>,
    pub state_path: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct EnglishVocabSetBookInput {
    pub book_id: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct EnglishVocabPrefetchInput {
    pub sentence: String,
    pub book_id: Option<String>,
    pub max_tokens: Option<u32>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct EnglishVocabFrontendTraceInput {
    pub stage: String,
    pub detail: Option<Value>,
}

#[derive(Debug, Clone, Serialize)]
pub struct EnglishVocabPrefetchResult {
    pub started: bool,
    pub queued: u32,
    pub skipped_cached: u32,
}

#[derive(Debug, Clone, Deserialize)]
pub struct EnglishVocabReviewInput {
    pub book_id: String,
    pub word: String,
    pub rating: String,
    pub day: String,
    pub now_ms: u64,
}

impl Default for EnglishVocabState {
    fn default() -> Self {
        Self {
            selected_book_id: "daily_life_ngsl".to_string(),
            progress: BTreeMap::new(),
            daily: BTreeMap::new(),
            state_path: english_vocab_state_path().display().to_string(),
        }
    }
}

#[tauri::command]
pub async fn english_vocab_lookup(
    input: EnglishVocabLookupInput,
) -> Result<EnglishVocabLookupResult, String> {
    tauri::async_runtime::spawn_blocking(move || english_vocab_lookup_sync(input))
        .await
        .map_err(|e| e.to_string())?
}

#[tauri::command]
pub fn english_vocab_frontend_trace(input: EnglishVocabFrontendTraceInput) -> Result<(), String> {
    let stage = sanitize_trace_stage(&input.stage);
    english_example_chain_trace(
        &format!("frontend_{stage}"),
        input.detail.unwrap_or_else(|| json!({})),
    );
    Ok(())
}

fn english_vocab_lookup_sync(
    input: EnglishVocabLookupInput,
) -> Result<EnglishVocabLookupResult, String> {
    let lookup_started = Instant::now();
    let word = input.word.trim().to_string();
    if word.is_empty() {
        return Err("word is empty".to_string());
    }
    let book_id = input
        .book_id
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .unwrap_or("daily_life_ngsl")
        .to_string();
    let context_sentence = input
        .context_sentence
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(ToString::to_string);
    let require_final_example =
        input.require_final_example.unwrap_or(false) && context_sentence.is_none();

    let mut cache = read_english_vocab_lookup_cache();
    let scoped_key = english_vocab_lookup_cache_key(&book_id, &word, context_sentence.as_deref());
    english_vocab_trace(
        "lookup_start",
        json!({
            "word": word,
            "book_id": book_id,
            "has_context": context_sentence.is_some(),
            "require_final_example": require_final_example,
            "scoped_key": scoped_key,
        }),
    );
    english_example_chain_trace(
        "lookup_start",
        json!({
            "word": word,
            "book_id": book_id,
            "has_context": context_sentence.is_some(),
            "require_final_example": require_final_example,
        }),
    );
    if let Some(cached) = cache.get(&scoped_key).cloned() {
        if !lookup_result_needs_large_model_fallback(&cached)
            && (!require_final_example || lookup_result_is_final_grade(&cached))
        {
            english_vocab_trace(
                "lookup_cache_hit_scoped",
                json!({
                    "word": word,
                    "book_id": book_id,
                    "source": cached.source.as_str(),
                    "model": cached.model.as_str(),
                    "elapsed_ms": lookup_started.elapsed().as_millis(),
                }),
            );
            english_example_chain_trace(
                "cache_hit_scoped",
                json!({
                    "word": word,
                    "book_id": book_id,
                    "source": cached.source.as_str(),
                    "model": cached.model.as_str(),
                    "example": cached.example.as_str(),
                    "example_cn": cached.example_cn.as_str(),
                    "final_grade": lookup_result_is_final_grade(&cached),
                }),
            );
            return Ok(cached);
        }
    }
    let plain_key = english_vocab_lookup_cache_key(&book_id, &word, None);
    if let Some(cached) = cache.get(&plain_key).cloned() {
        if !lookup_result_needs_large_model_fallback(&cached)
            && (!require_final_example || lookup_result_is_final_grade(&cached))
        {
            english_vocab_trace(
                "lookup_cache_hit_plain",
                json!({
                    "word": word,
                    "book_id": book_id,
                    "source": cached.source.as_str(),
                    "model": cached.model.as_str(),
                    "elapsed_ms": lookup_started.elapsed().as_millis(),
                }),
            );
            english_example_chain_trace(
                "cache_hit_plain",
                json!({
                    "word": word,
                    "book_id": book_id,
                    "source": cached.source.as_str(),
                    "model": cached.model.as_str(),
                    "example": cached.example.as_str(),
                    "example_cn": cached.example_cn.as_str(),
                    "final_grade": lookup_result_is_final_grade(&cached),
                }),
            );
            return Ok(cached);
        }
    }

    let normalized_input = EnglishVocabLookupInput {
        word: word.clone(),
        book_id: Some(book_id.clone()),
        context_sentence: context_sentence.clone(),
        require_final_example: Some(require_final_example),
    };

    if require_final_example {
        match lookup_with_english_vocab_service_cache_only(&normalized_input) {
            Ok(result) if lookup_result_is_final_grade(&result) => {
                cache_insert_lookup_result(
                    &mut cache,
                    &book_id,
                    &word,
                    context_sentence.as_deref(),
                    &result,
                );
                let _ = write_english_vocab_lookup_cache(&cache);
                english_vocab_trace(
                    "lookup_service_cache_only_hit",
                    json!({
                        "word": word,
                        "book_id": book_id,
                        "source": result.source.as_str(),
                        "model": result.model.as_str(),
                        "elapsed_ms": lookup_started.elapsed().as_millis(),
                    }),
                );
                english_example_chain_trace(
                    "service_cache_only_hit_before_remote",
                    json!({
                        "word": word,
                        "book_id": book_id,
                        "source": result.source.as_str(),
                        "model": result.model.as_str(),
                        "example": result.example.as_str(),
                        "example_cn": result.example_cn.as_str(),
                        "elapsed_ms": lookup_started.elapsed().as_millis(),
                    }),
                );
                return Ok(result);
            }
            Ok(result) => {
                english_example_chain_trace(
                    "service_cache_only_rejected",
                    json!({
                        "word": word,
                        "book_id": book_id,
                        "source": result.source.as_str(),
                        "model": result.model.as_str(),
                        "example": result.example.as_str(),
                        "example_cn": result.example_cn.as_str(),
                        "elapsed_ms": lookup_started.elapsed().as_millis(),
                    }),
                );
            }
            Err(err) => {
                english_example_chain_trace(
                    "service_cache_only_miss",
                    json!({
                        "word": word,
                        "book_id": book_id,
                        "error": err,
                        "elapsed_ms": lookup_started.elapsed().as_millis(),
                    }),
                );
            }
        }
        match lookup_with_local_translate_or_example(&normalized_input) {
            Ok(result) if lookup_result_is_final_grade(&result) => {
                cache_insert_lookup_result(
                    &mut cache,
                    &book_id,
                    &word,
                    context_sentence.as_deref(),
                    &result,
                );
                let _ = write_english_vocab_lookup_cache(&cache);
                remember_lookup_in_vocab_service(&book_id, &word, &result);
                english_example_chain_trace(
                    "final_example_service_returned",
                    json!({
                        "word": word,
                        "book_id": book_id,
                        "source": result.source.as_str(),
                        "model": result.model.as_str(),
                        "example": result.example.as_str(),
                        "example_cn": result.example_cn.as_str(),
                        "elapsed_ms": lookup_started.elapsed().as_millis(),
                    }),
                );
                english_vocab_trace(
                    "lookup_final_example_service_returned",
                    json!({
                        "word": word,
                        "book_id": book_id,
                        "source": result.source.as_str(),
                        "model": result.model.as_str(),
                        "elapsed_ms": lookup_started.elapsed().as_millis(),
                    }),
                );
                return Ok(result);
            }
            Ok(result) => {
                english_example_chain_trace(
                    "final_example_service_rejected",
                    json!({
                        "word": word,
                        "book_id": book_id,
                        "source": result.source.as_str(),
                        "model": result.model.as_str(),
                        "example": result.example.as_str(),
                        "example_cn": result.example_cn.as_str(),
                        "elapsed_ms": lookup_started.elapsed().as_millis(),
                    }),
                );
            }
            Err(err) => {
                english_example_chain_trace(
                    "final_example_service_failed",
                    json!({
                        "word": word,
                        "book_id": book_id,
                        "error": err,
                        "elapsed_ms": lookup_started.elapsed().as_millis(),
                    }),
                );
            }
        }
        return Err("final example service failed; qwen-turbo card was not returned".to_string());
    }

    if require_final_example
        && english_vocab_rust_remote_fallback_enabled()
        && english_vocab_remote_lookup_enabled()
    {
        let remote_input = normalized_input.clone();
        english_example_chain_trace(
            "remote_qwen_turbo_start",
            json!({
                "word": word,
                "book_id": book_id,
                "require_final_example": require_final_example,
                "mode": "foreground_first",
                "elapsed_ms": lookup_started.elapsed().as_millis(),
            }),
        );
        match lookup_with_dashscope(remote_input) {
            Ok(result) => {
                cache_insert_lookup_result(
                    &mut cache,
                    &book_id,
                    &word,
                    context_sentence.as_deref(),
                    &result,
                );
                let _ = write_english_vocab_lookup_cache(&cache);
                remember_lookup_in_vocab_service(&book_id, &word, &result);
                english_example_chain_trace(
                    "remote_qwen_turbo_returned",
                    json!({
                        "word": word,
                        "book_id": book_id,
                        "source": result.source.as_str(),
                        "model": result.model.as_str(),
                        "example": result.example.as_str(),
                        "example_cn": result.example_cn.as_str(),
                        "mode": "foreground_first",
                        "elapsed_ms": lookup_started.elapsed().as_millis(),
                    }),
                );
                english_vocab_trace(
                    "lookup_remote_first_returned",
                    json!({
                        "word": word,
                        "book_id": book_id,
                        "source": result.source.as_str(),
                        "model": result.model.as_str(),
                        "elapsed_ms": lookup_started.elapsed().as_millis(),
                    }),
                );
                return Ok(result);
            }
            Err(remote_err) => {
                english_example_chain_trace(
                    "remote_final_example_failed",
                    json!({
                        "word": word,
                        "book_id": book_id,
                        "error": remote_err,
                        "mode": "foreground_first",
                        "elapsed_ms": lookup_started.elapsed().as_millis(),
                    }),
                );
            }
        }
    }

    let local_result = lookup_with_local_translate_or_example(&normalized_input).ok();
    if let Some(result) = local_result.as_ref() {
        if !lookup_result_needs_large_model_fallback(result)
            && (!require_final_example || lookup_result_is_final_grade(result))
        {
            cache_insert_lookup_result(
                &mut cache,
                &book_id,
                &word,
                context_sentence.as_deref(),
                result,
            );
            let _ = write_english_vocab_lookup_cache(&cache);
            english_vocab_trace(
                "lookup_local_success",
                json!({
                    "word": word,
                    "book_id": book_id,
                    "source": result.source.as_str(),
                    "model": result.model.as_str(),
                    "elapsed_ms": lookup_started.elapsed().as_millis(),
                }),
            );
            english_example_chain_trace(
                "local_service_success",
                json!({
                    "word": word,
                    "book_id": book_id,
                    "source": result.source.as_str(),
                    "model": result.model.as_str(),
                    "example": result.example.as_str(),
                    "example_cn": result.example_cn.as_str(),
                    "require_final_example": require_final_example,
                }),
            );
            return Ok(result.clone());
        }
    }

    if require_final_example && english_vocab_remote_lookup_enabled() {
        return Err(
            "qwen-turbo final example failed and local final example is not ready".to_string(),
        );
    }

    if !english_vocab_remote_lookup_enabled() {
        english_example_chain_trace(
            "remote_qwen_turbo_disabled",
            json!({
                "word": word,
                "book_id": book_id,
                "require_final_example": require_final_example,
                "has_local_result": local_result.is_some(),
                "elapsed_ms": lookup_started.elapsed().as_millis(),
            }),
        );
        if require_final_example && local_result.is_none() {
            return Err("final example is still preparing".to_string());
        }
        let result = local_result.unwrap_or_else(|| fallback_lookup_result(&normalized_input));
        cache_insert_lookup_result(
            &mut cache,
            &book_id,
            &word,
            context_sentence.as_deref(),
            &result,
        );
        let _ = write_english_vocab_lookup_cache(&cache);
        english_vocab_trace(
            "lookup_local_fallback_returned",
            json!({
                "word": word,
                "book_id": book_id,
                "source": result.source.as_str(),
                "model": result.model.as_str(),
                "elapsed_ms": lookup_started.elapsed().as_millis(),
            }),
        );
        english_example_chain_trace(
            "local_fallback_returned",
            json!({
                "word": word,
                "book_id": book_id,
                "source": result.source.as_str(),
                "model": result.model.as_str(),
                "example": result.example.as_str(),
                "example_cn": result.example_cn.as_str(),
                "require_final_example": require_final_example,
            }),
        );
        return Ok(result);
    }
    let remote_input = normalized_input.clone();
    english_example_chain_trace(
        "remote_qwen_turbo_start",
        json!({
            "word": word,
            "book_id": book_id,
            "require_final_example": require_final_example,
            "elapsed_ms": lookup_started.elapsed().as_millis(),
        }),
    );
    let result = lookup_with_dashscope(remote_input).or_else(|remote_err| {
            if require_final_example {
                english_example_chain_trace(
                    "remote_final_example_failed",
                    json!({
                        "word": word,
                        "book_id": book_id,
                        "error": remote_err,
                        "elapsed_ms": lookup_started.elapsed().as_millis(),
                    }),
                );
                return Err("final example model fallback failed".to_string());
            }
            Ok::<EnglishVocabLookupResult, String>(
                local_result
                    .clone()
                    .filter(|result| !lookup_result_needs_large_model_fallback(result))
                    .unwrap_or_else(|| fallback_lookup_result(&normalized_input)),
            )
        })?;
    cache_insert_lookup_result(
        &mut cache,
        &book_id,
        &word,
        context_sentence.as_deref(),
        &result,
    );
    let _ = write_english_vocab_lookup_cache(&cache);
    english_example_chain_trace(
        "remote_qwen_turbo_returned",
        json!({
            "word": word,
            "book_id": book_id,
            "source": result.source.as_str(),
            "model": result.model.as_str(),
            "example": result.example.as_str(),
            "example_cn": result.example_cn.as_str(),
            "elapsed_ms": lookup_started.elapsed().as_millis(),
        }),
    );
    english_vocab_trace(
        "lookup_remote_or_fallback_returned",
        json!({
            "word": word,
            "book_id": book_id,
            "source": result.source.as_str(),
            "model": result.model.as_str(),
            "elapsed_ms": lookup_started.elapsed().as_millis(),
        }),
    );
    english_example_chain_trace(
        "remote_or_fallback_returned",
        json!({
            "word": word,
            "book_id": book_id,
            "source": result.source.as_str(),
            "model": result.model.as_str(),
            "example": result.example.as_str(),
            "example_cn": result.example_cn.as_str(),
            "require_final_example": require_final_example,
        }),
    );
    Ok(result)
}

#[tauri::command]
pub fn english_vocab_warmup() -> Result<Value, String> {
    english_vocab_service_post(
        "/warmup",
        &json!({"direction": "en-zh"}),
        Duration::from_secs(8),
    )
}

fn fallback_lookup_result(input: &EnglishVocabLookupInput) -> EnglishVocabLookupResult {
    let word = normalize_token(&input.word);
    let clean_word = if word.is_empty() {
        input.word.trim().to_string()
    } else {
        word
    };
    let example = input
        .context_sentence
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(ToString::to_string)
        .unwrap_or_default();
    let meaning = translate_with_local_model(&clean_word)
        .ok()
        .map(|x| x.trim().to_string())
        .filter(|x| !x.is_empty() && contains_cjk(x))
        .unwrap_or_else(|| format!("{}：词义正在准备，请稍后重试。", clean_word));
    EnglishVocabLookupResult {
        word: clean_word,
        phonetic: "-".to_string(),
        part_of_speech: "-".to_string(),
        meaning_cn: meaning,
        example,
        example_cn: input
            .context_sentence
            .as_deref()
            .and_then(|sentence| translate_with_local_model(sentence).ok())
            .unwrap_or_default(),
        source: "local_context_fallback".to_string(),
        model: "local_fallback".to_string(),
        refresh_hint: None,
    }
}

#[tauri::command]
pub fn english_vocab_prefetch_sentence(
    input: EnglishVocabPrefetchInput,
) -> Result<EnglishVocabPrefetchResult, String> {
    let sentence = input.sentence.trim().to_string();
    if sentence.is_empty() {
        return Ok(EnglishVocabPrefetchResult {
            started: false,
            queued: 0,
            skipped_cached: 0,
        });
    }
    let book_id = input
        .book_id
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .unwrap_or("daily_life_ngsl")
        .to_string();
    let max_tokens = input.max_tokens.unwrap_or(14).clamp(1, 48) as usize;
    let tokens = extract_sentence_tokens(&sentence, max_tokens);
    if tokens.is_empty() {
        return Ok(EnglishVocabPrefetchResult {
            started: false,
            queued: 0,
            skipped_cached: 0,
        });
    }

    let cache = read_english_vocab_lookup_cache();
    let mut pending = Vec::new();
    let mut skipped_cached = 0u32;
    for token in tokens {
        let scoped_key = english_vocab_lookup_cache_key(&book_id, &token, Some(&sentence));
        let plain_key = english_vocab_lookup_cache_key(&book_id, &token, None);
        if cache.contains_key(&scoped_key) || cache.contains_key(&plain_key) {
            skipped_cached = skipped_cached.saturating_add(1);
        } else {
            pending.push(token);
        }
    }
    if pending.is_empty() {
        return Ok(EnglishVocabPrefetchResult {
            started: false,
            queued: 0,
            skipped_cached,
        });
    }

    let queued = pending.len() as u32;
    let pending_bg = pending.clone();
    let sentence_bg = sentence.clone();
    let book_id_bg = book_id.clone();
    thread::Builder::new()
        .name("english-vocab-prefetch".to_string())
        .spawn(move || {
            let mut cache = read_english_vocab_lookup_cache();
            let mut changed = false;
            let sentence_cn = translate_with_local_model_output(&sentence_bg)
                .map(|x| x.translation)
                .unwrap_or_default();

            for token in pending_bg {
                let scoped_key =
                    english_vocab_lookup_cache_key(&book_id_bg, &token, Some(&sentence_bg));
                if cache.contains_key(&scoped_key) {
                    continue;
                }
                let req = EnglishVocabLookupInput {
                    word: token.clone(),
                    book_id: Some(book_id_bg.clone()),
                    context_sentence: Some(sentence_bg.clone()),
                    require_final_example: Some(false),
                };
                if let Ok(result) = lookup_with_local_translate_or_example(&req) {
                    let result = if result.example_cn.trim().is_empty() && !sentence_cn.is_empty() {
                        EnglishVocabLookupResult {
                            example_cn: sentence_cn.clone(),
                            ..result
                        }
                    } else {
                        result
                    };
                    cache_insert_lookup_result(
                        &mut cache,
                        &book_id_bg,
                        &token,
                        Some(&sentence_bg),
                        &result,
                    );
                    changed = true;
                }
            }
            if changed {
                let _ = write_english_vocab_lookup_cache(&cache);
            }
        })
        .map_err(|e| format!("spawn prefetch thread failed: {e}"))?;

    Ok(EnglishVocabPrefetchResult {
        started: true,
        queued,
        skipped_cached,
    })
}

fn lookup_with_local_translate_or_example(
    input: &EnglishVocabLookupInput,
) -> Result<EnglishVocabLookupResult, String> {
    let require_final_example = input.require_final_example.unwrap_or(false)
        && input
            .context_sentence
            .as_deref()
            .map(str::trim)
            .unwrap_or("")
            .is_empty();
    match lookup_with_english_vocab_service(input) {
        Ok(result) => return Ok(result),
        Err(err) => {
            english_example_chain_trace(
                "local_service_not_ready",
                json!({
                    "word": input.word.trim(),
                    "book_id": input.book_id.as_deref().unwrap_or("daily_life_ngsl"),
                    "require_final_example": require_final_example,
                    "error": err,
                }),
            );
            if require_final_example {
                return Err("final example is not ready from local service".to_string());
            }
        }
    }
    if let Ok(result) = lookup_with_local_example_model(&input) {
        return Ok(result);
    }
    Err("local English example model is not ready".to_string())
}

#[tauri::command]
pub fn english_vocab_state_get() -> Result<EnglishVocabState, String> {
    read_english_vocab_state()
}

#[tauri::command]
pub fn english_vocab_state_set_book(
    input: EnglishVocabSetBookInput,
) -> Result<EnglishVocabState, String> {
    let mut state = read_english_vocab_state()?;
    let book_id = input.book_id.trim();
    if book_id.is_empty() {
        return Err("book_id is empty".to_string());
    }
    state.selected_book_id = book_id.to_string();
    write_english_vocab_state(&state)?;
    Ok(state)
}

#[tauri::command]
pub fn english_vocab_state_record_review(
    input: EnglishVocabReviewInput,
) -> Result<EnglishVocabState, String> {
    let mut state = read_english_vocab_state()?;
    let book_id = input.book_id.trim();
    let word = input.word.trim().to_ascii_lowercase();
    let rating = input.rating.trim();
    let day = input.day.trim();
    if book_id.is_empty() || word.is_empty() || day.is_empty() {
        return Err("book_id, word, and day are required".to_string());
    }
    if rating != "known" && rating != "fuzzy" && rating != "unknown" {
        return Err("rating must be known, fuzzy, or unknown".to_string());
    }
    state.selected_book_id = book_id.to_string();
    let key = format!("{book_id}:{word}");
    let mut progress = state
        .progress
        .get(&key)
        .cloned()
        .unwrap_or(EnglishVocabProgress {
            seen: 0,
            known: 0,
            fuzzy: 0,
            unknown: 0,
            status: "new".to_string(),
            last_seen_at: None,
            due_at: None,
        });
    progress.seen = progress.seen.saturating_add(1);
    if rating == "known" {
        progress.known = progress.known.saturating_add(1);
    } else if rating == "fuzzy" {
        progress.fuzzy = progress.fuzzy.saturating_add(1);
    } else {
        progress.unknown = progress.unknown.saturating_add(1);
    }
    progress.status = if rating == "known" && progress.known >= 2 && progress.unknown == 0 {
        "known".to_string()
    } else {
        "learning".to_string()
    };
    let interval_minutes: u64 = if rating == "known" {
        if progress.known >= 2 {
            24 * 60
        } else {
            6 * 60
        }
    } else if rating == "fuzzy" {
        60
    } else {
        10
    };
    progress.last_seen_at = Some(input.now_ms);
    progress.due_at = Some(input.now_ms.saturating_add(interval_minutes * 60 * 1000));
    state.progress.insert(key, progress);

    let stats = state.daily.entry(day.to_string()).or_default();
    stats.total = stats.total.saturating_add(1);
    if rating == "known" {
        stats.known = stats.known.saturating_add(1);
    } else if rating == "fuzzy" {
        stats.fuzzy = stats.fuzzy.saturating_add(1);
    } else {
        stats.unknown = stats.unknown.saturating_add(1);
    }

    write_english_vocab_state(&state)?;
    Ok(state)
}

#[tauri::command]
pub fn english_vocab_state_reset() -> Result<EnglishVocabState, String> {
    let mut state = read_english_vocab_state().unwrap_or_default();
    state.progress.clear();
    state.daily.clear();
    write_english_vocab_state(&state)?;
    Ok(state)
}

fn lookup_with_dashscope(
    input: EnglishVocabLookupInput,
) -> Result<EnglishVocabLookupResult, String> {
    let env = merged_env_values();
    let active_region = env
        .get("JACHIN_ACTIVE_REGION")
        .map(|s| s.trim().to_ascii_uppercase())
        .unwrap_or_default();
    let api_key = if active_region == "SEA" {
        first_non_empty(
            &env,
            &[
                "DASHSCOPE_API_KEY_SEA",
                "DASHSCOPE_API_KEY",
                "QWEN_API_KEY",
                "QWEN_AI_API_KEY",
            ],
        )
    } else {
        first_non_empty(
            &env,
            &[
                "DASHSCOPE_API_KEY_CN",
                "DASHSCOPE_API_KEY",
                "QWEN_API_KEY",
                "QWEN_AI_API_KEY",
            ],
        )
    }
    .ok_or_else(|| {
        "DashScope API key not found. Please configure DASHSCOPE_API_KEY or QWEN_API_KEY."
            .to_string()
    })?;
    let api_base = if active_region == "SEA" {
        first_non_empty(
            &env,
            &[
                "JACHIN_ENGLISH_VOCAB_API_BASE",
                "DASHSCOPE_API_BASE_SEA",
                "DASHSCOPE_API_BASE",
            ],
        )
    } else {
        first_non_empty(
            &env,
            &[
                "JACHIN_ENGLISH_VOCAB_API_BASE",
                "DASHSCOPE_API_BASE_CN",
                "DASHSCOPE_API_BASE",
            ],
        )
    }
    .unwrap_or_else(|| {
        if active_region == "SEA" {
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1".to_string()
        } else {
            "https://dashscope.aliyuncs.com/compatible-mode/v1".to_string()
        }
    });
    let model = first_non_empty(&env, &["JACHIN_ENGLISH_VOCAB_MODEL"])
        .unwrap_or_else(|| "qwen-turbo".to_string())
        .trim_start_matches("dashscope/")
        .to_string();
    let timeout_ms = first_non_empty(&env, &["JACHIN_ENGLISH_VOCAB_REMOTE_TIMEOUT_MS"])
        .and_then(|raw| raw.trim().parse::<u64>().ok())
        .unwrap_or(4_500)
        .clamp(1_500, 12_000);

    let prompt = build_prompt(&input);
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_millis(timeout_ms))
        .build()
        .map_err(|e| format!("create HTTP client failed: {e}"))?;
    let url = format!("{}/chat/completions", api_base.trim_end_matches('/'));
    let request_started = Instant::now();
    english_example_chain_trace(
        "remote_qwen_http_send",
        json!({
            "word": input.word.trim(),
            "model": model.as_str(),
            "api_base": api_base.as_str(),
            "timeout_ms": timeout_ms,
        }),
    );
    let resp = match client
        .post(url)
        .bearer_auth(api_key)
        .json(&json!({
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a precise English vocabulary tutor. Return strict JSON only. Do not wrap in markdown."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": 0.2,
            "max_tokens": 180
        }))
        .send()
    {
        Ok(resp) => {
            english_example_chain_trace(
                "remote_qwen_http_status",
                json!({
                    "word": input.word.trim(),
                    "model": model.as_str(),
                    "status": resp.status().as_u16(),
                    "elapsed_ms": request_started.elapsed().as_millis(),
                }),
            );
            resp
        }
        Err(e) => {
            english_example_chain_trace(
                "remote_qwen_http_error",
                json!({
                    "word": input.word.trim(),
                    "model": model.as_str(),
                    "error": e.to_string(),
                    "elapsed_ms": request_started.elapsed().as_millis(),
                }),
            );
            return Err(format!("DashScope request failed: {e}"));
        }
    };
    if !resp.status().is_success() {
        let status = resp.status().as_u16();
        let body = resp.text().unwrap_or_default();
        return Err(format!(
            "DashScope HTTP {status}: {}",
            body.chars().take(300).collect::<String>()
        ));
    }
    let value: Value = resp
        .json()
        .map_err(|e| format!("parse DashScope response failed: {e}"))?;
    let content = value
        .get("choices")
        .and_then(Value::as_array)
        .and_then(|arr| arr.first())
        .and_then(|c| c.get("message"))
        .and_then(|m| m.get("content"))
        .and_then(Value::as_str)
        .ok_or_else(|| "DashScope response has no content".to_string())?;
    let json_text = extract_json_object(content);
    let parsed: Value = serde_json::from_str(&json_text).map_err(|e| {
        format!(
            "model did not return valid JSON: {e}; content={}",
            content.chars().take(300).collect::<String>()
        )
    })?;
    let result = EnglishVocabLookupResult {
        word: value_string(&parsed, "word").unwrap_or_else(|| input.word.trim().to_string()),
        phonetic: value_string(&parsed, "phonetic").unwrap_or_else(|| "-".to_string()),
        part_of_speech: value_string(&parsed, "part_of_speech").unwrap_or_else(|| "-".to_string()),
        meaning_cn: value_string(&parsed, "meaning_cn")
            .unwrap_or_else(|| "模型已响应，但释义生成不完整，请重试。".to_string()),
        example: value_string(&parsed, "example").unwrap_or_default(),
        example_cn: value_string(&parsed, "example_cn").unwrap_or_else(|| "".to_string()),
        source: "dashscope".to_string(),
        model,
        refresh_hint: None,
    };
    if lookup_result_needs_large_model_fallback(&result) {
        return Err("DashScope returned incomplete English vocab card".to_string());
    }
    Ok(result)
}

fn english_vocab_remote_lookup_enabled() -> bool {
    let env = merged_env_values();
    if let Some(value) = first_non_empty(&env, &["JACHIN_ENGLISH_VOCAB_ALLOW_REMOTE"]) {
        let flag = value.trim().to_ascii_lowercase();
        return !matches!(flag.as_str(), "0" | "false" | "no" | "off" | "disabled");
    }
    first_non_empty(
        &env,
        &[
            "DASHSCOPE_API_KEY_CN",
            "DASHSCOPE_API_KEY_SEA",
            "DASHSCOPE_API_KEY",
            "QWEN_API_KEY",
            "QWEN_AI_API_KEY",
        ],
    )
    .is_some()
}

fn english_vocab_rust_remote_fallback_enabled() -> bool {
    let env = merged_env_values();
    let Some(value) = first_non_empty(&env, &["JACHIN_ENGLISH_VOCAB_RUST_REMOTE_FALLBACK"])
    else {
        return false;
    };
    matches!(
        value.trim().to_ascii_lowercase().as_str(),
        "1" | "true" | "yes" | "on" | "enabled"
    )
}

static ENGLISH_VOCAB_SERVICE_CHILD: OnceLock<Mutex<Option<Child>>> = OnceLock::new();

fn english_vocab_service_child() -> &'static Mutex<Option<Child>> {
    ENGLISH_VOCAB_SERVICE_CHILD.get_or_init(|| Mutex::new(None))
}

pub fn shutdown_english_vocab_service() {
    let Ok(mut guard) = english_vocab_service_child().lock() else {
        return;
    };
    let Some(mut child) = guard.take() else {
        return;
    };
    match child.try_wait() {
        Ok(Some(_)) => {}
        Ok(None) => {
            let pid = child.id();
            english_vocab_trace("service_shutdown_kill", json!({ "pid": pid }));
            let _ = child.kill();
            let _ = child.wait();
        }
        Err(e) => {
            english_vocab_trace(
                "service_shutdown_wait_failed",
                json!({ "error": e.to_string() }),
            );
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

fn english_vocab_service_port() -> u16 {
    first_non_empty(&merged_env_values(), &["JACHIN_ENGLISH_VOCAB_SERVICE_PORT"])
        .and_then(|v| v.trim().parse::<u16>().ok())
        .unwrap_or(18987)
}

fn english_vocab_service_url() -> String {
    first_non_empty(&merged_env_values(), &["JACHIN_ENGLISH_VOCAB_SERVICE_URL"])
        .unwrap_or_else(|| format!("http://127.0.0.1:{}", english_vocab_service_port()))
        .trim_end_matches('/')
        .to_string()
}

fn english_vocab_service_health(url: &str, timeout: Duration) -> bool {
    let client = match reqwest::blocking::Client::builder()
        .timeout(timeout)
        .build()
    {
        Ok(client) => client,
        Err(_) => return false,
    };
    client
        .get(format!("{}/health", url.trim_end_matches('/')))
        .send()
        .map(|resp| resp.status().is_success())
        .unwrap_or(false)
}

fn ensure_english_vocab_service() -> Result<String, String> {
    let url = english_vocab_service_url();
    if english_vocab_service_health(&url, Duration::from_millis(450)) {
        english_vocab_trace("service_health_ok", json!({"url": url}));
        return Ok(url);
    }
    if std::env::var("JACHIN_ENGLISH_VOCAB_SERVICE_URL")
        .map(|v| !v.trim().is_empty())
        .unwrap_or(false)
    {
        return Err(format!("English vocab service is not reachable: {url}"));
    }

    let mut guard = english_vocab_service_child()
        .lock()
        .map_err(|_| "English vocab service child lock poisoned".to_string())?;
    if let Some(child) = guard.as_mut() {
        match child.try_wait() {
            Ok(None) => {}
            Ok(Some(_)) | Err(_) => {
                *guard = None;
            }
        }
    }
    if english_vocab_service_health(&url, Duration::from_millis(450)) {
        english_vocab_trace("service_health_ok_after_lock", json!({"url": url}));
        return Ok(url);
    }

    let dir =
        local_translate_dir().ok_or_else(|| "local translate MCP dir not found".to_string())?;
    let service = dir.join("english_vocab_service.py");
    if !service.is_file() {
        return Err(format!(
            "English vocab service script missing: {}",
            service.display()
        ));
    }
    let python = local_mcp_python_command();
    let mut cmd = Command::new(python);
    cmd.arg("-u")
        .arg(&service)
        .arg("--host")
        .arg("127.0.0.1")
        .arg("--port")
        .arg(english_vocab_service_port().to_string());
    cmd.env("JACHIN_HOME", jachin_home_dir());
    cmd.env(
        "JACHIN_ENGLISH_VOCAB_SERVICE_LOG",
        english_vocab_service_log_path(),
    );
    let mut python_paths = vec![dir.clone()];
    if let Some(example_dir) = local_example_generator_dir() {
        python_paths.push(example_dir);
    }
    if let Ok(joined) = std::env::join_paths(python_paths.iter()) {
        cmd.env("PYTHONPATH", joined);
    }
    #[cfg(target_os = "windows")]
    {
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    let stdout_log = service_stdio_log("stdout").ok();
    let stderr_log = service_stdio_log("stderr").ok();
    cmd.stdin(Stdio::null());
    if let Some(file) = stdout_log {
        cmd.stdout(Stdio::from(file));
    } else {
        cmd.stdout(Stdio::null());
    }
    if let Some(file) = stderr_log {
        cmd.stderr(Stdio::from(file));
    } else {
        cmd.stderr(Stdio::null());
    }
    english_vocab_trace(
        "service_spawn_start",
        json!({
            "url": url,
            "script": service.display().to_string(),
            "port": english_vocab_service_port(),
            "log": english_vocab_service_log_path().display().to_string(),
        }),
    );
    let child = cmd.spawn().map_err(|e| {
        english_vocab_trace("service_spawn_failed", json!({"error": e.to_string()}));
        format!("spawn English vocab service failed: {e}")
    })?;
    *guard = Some(child);
    drop(guard);

    let start = Instant::now();
    while start.elapsed() < Duration::from_secs(7) {
        if english_vocab_service_health(&url, Duration::from_millis(500)) {
            english_vocab_trace(
                "service_ready",
                json!({
                    "url": url,
                    "elapsed_ms": start.elapsed().as_millis(),
                }),
            );
            return Ok(url);
        }
        thread::sleep(Duration::from_millis(120));
    }
    english_vocab_trace(
        "service_ready_timeout",
        json!({
            "url": url,
            "elapsed_ms": start.elapsed().as_millis(),
            "log": english_vocab_service_log_path().display().to_string(),
        }),
    );
    Err(format!("English vocab service did not become ready: {url}"))
}

fn english_vocab_service_post(
    endpoint: &str,
    payload: &Value,
    timeout: Duration,
) -> Result<Value, String> {
    let started = Instant::now();
    let url = ensure_english_vocab_service()?;
    let client = reqwest::blocking::Client::builder()
        .timeout(timeout)
        .build()
        .map_err(|e| format!("create English vocab service HTTP client failed: {e}"))?;
    let resp = client
        .post(format!("{}{}", url, endpoint))
        .json(payload)
        .send()
        .map_err(|e| {
            english_vocab_trace(
                "service_request_failed",
                json!({
                    "endpoint": endpoint,
                    "error": e.to_string(),
                    "elapsed_ms": started.elapsed().as_millis(),
                }),
            );
            format!("English vocab service request failed: {e}")
        })?;
    let status = resp.status();
    let body = resp
        .text()
        .map_err(|e| format!("read English vocab service response failed: {e}"))?;
    let value: Value = serde_json::from_str(&body).map_err(|e| {
        format!(
            "parse English vocab service response failed: {e}; body={}",
            body.chars().take(300).collect::<String>()
        )
    })?;
    if !status.is_success() || !value.get("ok").and_then(Value::as_bool).unwrap_or(false) {
        let error = value_string(&value, "error").unwrap_or_else(|| {
            format!(
                "English vocab service returned HTTP {}: {}",
                status.as_u16(),
                body.chars().take(300).collect::<String>()
            )
        });
        english_vocab_trace(
            "service_response_not_ok",
            json!({
                "endpoint": endpoint,
                "status": status.as_u16(),
                "error": error.as_str(),
                "elapsed_ms": started.elapsed().as_millis(),
            }),
        );
        return Err(error);
    }
    english_vocab_trace(
        "service_response_ok",
        json!({
            "endpoint": endpoint,
            "status": status.as_u16(),
            "elapsed_ms": started.elapsed().as_millis(),
        }),
    );
    Ok(value)
}

fn english_vocab_service_post_if_running(
    endpoint: &str,
    payload: &Value,
    timeout: Duration,
) -> Result<Value, String> {
    let url = english_vocab_service_url();
    if !english_vocab_service_health(&url, Duration::from_millis(120)) {
        return Err("English vocab service is not running".to_string());
    }
    let client = reqwest::blocking::Client::builder()
        .timeout(timeout)
        .build()
        .map_err(|e| format!("create English vocab service HTTP client failed: {e}"))?;
    let resp = client
        .post(format!("{}{}", url, endpoint))
        .json(payload)
        .send()
        .map_err(|e| format!("English vocab service request failed: {e}"))?;
    let status = resp.status();
    let body = resp
        .text()
        .map_err(|e| format!("read English vocab service response failed: {e}"))?;
    let value: Value = serde_json::from_str(&body).map_err(|e| {
        format!(
            "parse English vocab service response failed: {e}; body={}",
            body.chars().take(300).collect::<String>()
        )
    })?;
    if !status.is_success() || !value.get("ok").and_then(Value::as_bool).unwrap_or(false) {
        return Err(value_string(&value, "error").unwrap_or_else(|| {
            format!(
                "English vocab service returned HTTP {}: {}",
                status.as_u16(),
                body.chars().take(300).collect::<String>()
            )
        }));
    }
    Ok(value)
}

fn english_vocab_final_service_timeout() -> Duration {
    let ms = std::env::var("JACHIN_ENGLISH_VOCAB_FINAL_SERVICE_TIMEOUT_MS")
        .ok()
        .and_then(|raw| raw.trim().parse::<u64>().ok())
        .unwrap_or(2_800)
        .clamp(900, 8_000);
    Duration::from_millis(ms)
}

fn lookup_with_english_vocab_service(
    input: &EnglishVocabLookupInput,
) -> Result<EnglishVocabLookupResult, String> {
    let started = Instant::now();
    let require_final_example = input.require_final_example.unwrap_or(false);
    let payload = json!({
        "word": input.word.trim(),
        "book_id": input.book_id.as_deref().unwrap_or("daily_life_ngsl"),
        "context_sentence": input.context_sentence.as_deref().unwrap_or(""),
        "require_final_example": require_final_example,
    });
    let value = if require_final_example {
        // Keep this below the UI timeout, but above the observed warm local
        // model latency. A 900ms ceiling caused valid model cards to be
        // discarded before the service could return them.
        english_vocab_service_post_if_running(
            "/lookup",
            &payload,
            english_vocab_final_service_timeout(),
        )
    } else {
        english_vocab_service_post("/lookup", &payload, Duration::from_secs(8))
    }?;
    let result = EnglishVocabLookupResult {
        word: value_string(&value, "word").unwrap_or_else(|| input.word.trim().to_string()),
        phonetic: value_string(&value, "phonetic").unwrap_or_else(|| "-".to_string()),
        part_of_speech: value_string(&value, "part_of_speech").unwrap_or_else(|| "-".to_string()),
        meaning_cn: value_string(&value, "meaning_cn").unwrap_or_default(),
        example: value_string(&value, "example").unwrap_or_default(),
        example_cn: value_string(&value, "example_cn").unwrap_or_default(),
        source: value_string(&value, "source")
            .unwrap_or_else(|| "english_vocab_service".to_string()),
        model: value_string(&value, "model").unwrap_or_else(|| "english_vocab_service".to_string()),
        refresh_hint: value_string(&value, "refresh_hint"),
    };
    if lookup_result_needs_large_model_fallback(&result) {
        english_vocab_trace(
            "service_lookup_incomplete",
            json!({
                "word": input.word.trim(),
                "source": result.source.as_str(),
                "model": result.model.as_str(),
                "elapsed_ms": started.elapsed().as_millis(),
            }),
        );
        return Err("English vocab service returned incomplete result".to_string());
    }
    english_vocab_trace(
        "service_lookup_success",
        json!({
            "word": input.word.trim(),
            "source": result.source.as_str(),
            "model": result.model.as_str(),
            "elapsed_ms": started.elapsed().as_millis(),
        }),
    );
    Ok(result)
}

fn lookup_with_english_vocab_service_cache_only(
    input: &EnglishVocabLookupInput,
) -> Result<EnglishVocabLookupResult, String> {
    let started = Instant::now();
    let payload = json!({
        "word": input.word.trim(),
        "book_id": input.book_id.as_deref().unwrap_or("daily_life_ngsl"),
        "context_sentence": "",
        "require_final_example": true,
        "cache_only": true,
    });
    let value =
        english_vocab_service_post_if_running("/lookup", &payload, Duration::from_millis(650))?;
    let result = EnglishVocabLookupResult {
        word: value_string(&value, "word").unwrap_or_else(|| input.word.trim().to_string()),
        phonetic: value_string(&value, "phonetic").unwrap_or_else(|| "-".to_string()),
        part_of_speech: value_string(&value, "part_of_speech").unwrap_or_else(|| "-".to_string()),
        meaning_cn: value_string(&value, "meaning_cn").unwrap_or_default(),
        example: value_string(&value, "example").unwrap_or_default(),
        example_cn: value_string(&value, "example_cn").unwrap_or_default(),
        source: value_string(&value, "source")
            .unwrap_or_else(|| "english_vocab_service_cache".to_string()),
        model: value_string(&value, "model")
            .unwrap_or_else(|| "english_vocab_service_cache".to_string()),
        refresh_hint: value_string(&value, "refresh_hint"),
    };
    if lookup_result_needs_large_model_fallback(&result) {
        english_vocab_trace(
            "service_cache_only_incomplete",
            json!({
                "word": input.word.trim(),
                "source": result.source.as_str(),
                "model": result.model.as_str(),
                "elapsed_ms": started.elapsed().as_millis(),
            }),
        );
        return Err("English vocab service cache returned incomplete result".to_string());
    }
    english_vocab_trace(
        "service_cache_only_success",
        json!({
            "word": input.word.trim(),
            "source": result.source.as_str(),
            "model": result.model.as_str(),
            "elapsed_ms": started.elapsed().as_millis(),
        }),
    );
    Ok(result)
}

fn lookup_with_local_example_model(
    input: &EnglishVocabLookupInput,
) -> Result<EnglishVocabLookupResult, String> {
    let cli = local_example_generator_cli_path()
        .ok_or_else(|| "local example generator cli not found".to_string())?;
    let python = local_mcp_python_command();
    let book_id = input.book_id.as_deref().unwrap_or("daily_life_ngsl");
    let local_meaning = String::new();
    let mut cmd = Command::new(python);
    cmd.arg(cli)
        .arg("generate")
        .arg("--word")
        .arg(input.word.trim())
        .arg("--book-id")
        .arg(book_id)
        .arg("--meaning-cn")
        .arg(local_meaning.as_str());
    let home = jachin_home_dir();
    cmd.env("JACHIN_HOME", home);
    if let Some(parent) = local_example_generator_dir() {
        cmd.env("PYTHONPATH", parent);
    }
    let output =
        command_output_with_timeout(cmd, Duration::from_secs(4), "local example generator")
            .map_err(|e| format!("local example generator failed to start: {e}"))?;
    if !output.status.success() {
        return Err(format!(
            "local example generator exited {}: {}",
            output.status,
            String::from_utf8_lossy(&output.stderr)
                .chars()
                .take(300)
                .collect::<String>()
        ));
    }
    let stdout = String::from_utf8_lossy(&output.stdout);
    let parsed: Value = serde_json::from_str(stdout.trim()).map_err(|e| {
        format!(
            "parse local example generator response failed: {e}; stdout={}",
            stdout.chars().take(300).collect::<String>()
        )
    })?;
    let ok = parsed.get("ok").and_then(Value::as_bool).unwrap_or(false);
    if !ok {
        return Err(value_string(&parsed, "error")
            .unwrap_or_else(|| "local example generator returned not ok".to_string()));
    }
    let example = value_string(&parsed, "example")
        .ok_or_else(|| "local example generator returned no example".to_string())?;
    let example_cn = translate_with_local_model(&example).unwrap_or_default();
    Ok(EnglishVocabLookupResult {
        word: input.word.trim().to_string(),
        phonetic: "-".to_string(),
        part_of_speech: "-".to_string(),
        meaning_cn: value_string(&parsed, "meaning_cn").unwrap_or(local_meaning),
        example,
        example_cn,
        source: value_string(&parsed, "source").unwrap_or_else(|| "local_gguf".to_string()),
        model: value_string(&parsed, "model_id").unwrap_or_else(|| "local_gguf".to_string()),
        refresh_hint: None,
    })
}

#[derive(Debug, Clone)]
struct LocalTranslateOutput {
    translation: String,
}

fn command_output_with_timeout(
    mut cmd: Command,
    timeout: Duration,
    label: &str,
) -> Result<Output, String> {
    #[cfg(target_os = "windows")]
    {
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    cmd.stdout(Stdio::piped()).stderr(Stdio::piped());
    let mut child = cmd
        .spawn()
        .map_err(|e| format!("{label} spawn failed: {e}"))?;
    let start = Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(_)) => {
                return child
                    .wait_with_output()
                    .map_err(|e| format!("{label} collect output failed: {e}"));
            }
            Ok(None) => {
                if start.elapsed() >= timeout {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err(format!("{label} timed out after {}ms", timeout.as_millis()));
                }
                thread::sleep(Duration::from_millis(25));
            }
            Err(e) => {
                let _ = child.kill();
                return Err(format!("{label} wait failed: {e}"));
            }
        }
    }
}

fn translate_with_local_model_output(text: &str) -> Result<LocalTranslateOutput, String> {
    if let Ok(value) = english_vocab_service_post(
        "/translate",
        &json!({"text": text, "direction": "en-zh"}),
        Duration::from_secs(5),
    ) {
        if let Some(translation) = value_string(&value, "translation") {
            return Ok(LocalTranslateOutput { translation });
        }
    }

    let cli =
        local_translate_cli_path().ok_or_else(|| "local translate cli not found".to_string())?;
    let python = local_mcp_python_command();
    let mut cmd = Command::new(python);
    cmd.arg(cli)
        .arg("translate")
        .arg("--text")
        .arg(text)
        .arg("--direction")
        .arg("en-zh");
    cmd.env("JACHIN_HOME", jachin_home_dir());
    if let Some(parent) = local_translate_dir() {
        cmd.env("PYTHONPATH", parent);
    }
    let output = command_output_with_timeout(cmd, Duration::from_secs(9), "local translate")
        .map_err(|e| format!("local translate failed to start: {e}"))?;
    if !output.status.success() {
        return Err(format!(
            "local translate exited {}: {}",
            output.status,
            String::from_utf8_lossy(&output.stderr)
                .chars()
                .take(300)
                .collect::<String>()
        ));
    }
    let stdout = String::from_utf8_lossy(&output.stdout);
    let parsed: Value = serde_json::from_str(stdout.trim()).map_err(|e| {
        format!(
            "parse local translate response failed: {e}; stdout={}",
            stdout.chars().take(300).collect::<String>()
        )
    })?;
    let ok = parsed.get("ok").and_then(Value::as_bool).unwrap_or(false);
    if !ok {
        return Err(value_string(&parsed, "error")
            .unwrap_or_else(|| "local translate returned not ok".to_string()));
    }
    let translation = value_string(&parsed, "translation")
        .ok_or_else(|| "local translate returned no translation".to_string())?;
    Ok(LocalTranslateOutput { translation })
}

fn translate_with_local_model(text: &str) -> Result<String, String> {
    translate_with_local_model_output(text).map(|x| x.translation)
}

fn build_prompt(input: &EnglishVocabLookupInput) -> String {
    let book = input.book_id.as_deref().unwrap_or("general");
    let context = input.context_sentence.as_deref().unwrap_or("");
    let scene = match book {
        "daily_life_ngsl" => {
            "daily spoken life: meals, travel, family, shopping, home, health, weather"
        }
        "workplace" => {
            "workplace communication: meetings, reports, feedback, deadlines, collaboration"
        }
        "computer_science" => {
            "software engineering: coding, debugging, deployment, systems, data, infrastructure"
        }
        "ielts_academic" => {
            "IELTS academic writing: society, education, environment, economy, culture"
        }
        "toefl_academic" => {
            "TOEFL campus and lecture scenarios: classes, research, assignments, labs, seminars"
        }
        _ => "natural modern English",
    };
    format!(
        "Create a useful English vocabulary card for the TARGET WORD.\nTarget word: {word}\nBook id: {book}\nScene: {scene}\nContext sentence for sense disambiguation only: {context}\nReturn strict compact JSON only with keys: word, phonetic, part_of_speech, meaning_cn, example, example_cn.\nRules:\n- Explain the TARGET WORD itself, not the whole context sentence.\n- If context is provided, choose the meaning and part of speech that fits that context.\n- meaning_cn must be concise Simplified Chinese, 4-24 Chinese chars, and must not be empty.\n- example must be an original, natural English sentence for the scene, 6-14 words.\n- example must use the exact target word naturally.\n- Do not use generic memory-learning sentences such as \"I want to remember...\" or \"learn the word...\".\n- example_cn must be a natural Simplified Chinese translation of example.\n- No markdown, no explanation, no extra keys.",
        word = input.word.trim(),
        book = book,
        scene = scene,
        context = context
    )
}

fn local_example_generator_dir() -> Option<PathBuf> {
    if let Some(root) = crate::l3_spawn::project_root() {
        let dir = root
            .join("l3_client")
            .join("local_mcps")
            .join("english_example_generator_mcp");
        if dir.is_dir() {
            return Some(dir);
        }
    }
    let home = jachin_home_dir();
    for dir in [
        home.join("local_mcps")
            .join("english_example_generator_mcp"),
        home.join("mcp").join("english_example_generator_mcp"),
        home.join("l3_mcp_cache")
            .join("com.jachin.mcp.english-example-generator"),
    ] {
        if dir.is_dir() {
            return Some(dir);
        }
    }
    None
}

fn local_example_generator_cli_path() -> Option<PathBuf> {
    let dir = local_example_generator_dir()?;
    let cli = dir.join("example_generator_cli.py");
    cli.is_file().then_some(cli)
}

fn local_translate_dir() -> Option<PathBuf> {
    if let Some(root) = crate::l3_spawn::project_root() {
        let dir = root
            .join("l3_client")
            .join("local_mcps")
            .join("local_translate_mcp");
        if dir.is_dir() {
            return Some(dir);
        }
    }
    let home = jachin_home_dir();
    for dir in [
        home.join("local_mcps").join("local_translate_mcp"),
        home.join("mcp").join("local_translate_mcp"),
        home.join("l3_mcp_cache")
            .join("com.jachin.mcp.local-translate"),
    ] {
        if dir.is_dir() {
            return Some(dir);
        }
    }
    None
}

fn local_translate_cli_path() -> Option<PathBuf> {
    let dir = local_translate_dir()?;
    let cli = dir.join("local_translate_cli.py");
    cli.is_file().then_some(cli)
}

fn local_mcp_python_command() -> String {
    if let Some(root) = crate::l3_spawn::project_root() {
        let bundled = root.join("runtime").join("python").join("python.exe");
        if bundled.is_file() {
            return bundled.display().to_string();
        }
    }
    first_non_empty(&merged_env_values(), &["JACHIN_MCP_PYTHON", "PYTHON"])
        .unwrap_or_else(|| "python".to_string())
}

fn english_vocab_state_path() -> PathBuf {
    jachin_home_dir()
        .join("data")
        .join("english_vocab")
        .join("state.json")
}

fn english_vocab_lookup_cache_path() -> PathBuf {
    jachin_home_dir()
        .join("data")
        .join("english_vocab")
        .join("lookup_cache.json")
}

fn read_english_vocab_lookup_cache() -> BTreeMap<String, EnglishVocabLookupResult> {
    let path = english_vocab_lookup_cache_path();
    let Ok(content) = fs::read_to_string(path) else {
        return BTreeMap::new();
    };
    if let Ok(parsed) = serde_json::from_str::<BTreeMap<String, EnglishVocabLookupResult>>(&content)
    {
        return parsed;
    }
    let Ok(value) = serde_json::from_str::<Value>(&content) else {
        return BTreeMap::new();
    };
    value
        .get("items")
        .and_then(|v| {
            serde_json::from_value::<BTreeMap<String, EnglishVocabLookupResult>>(v.clone()).ok()
        })
        .unwrap_or_default()
}

fn write_english_vocab_lookup_cache(
    cache: &BTreeMap<String, EnglishVocabLookupResult>,
) -> Result<(), String> {
    let path = english_vocab_lookup_cache_path();
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|e| format!("create english vocab cache dir failed: {e}"))?;
    }
    let payload = json!({
        "version": 1,
        "items": cache,
    });
    let content = serde_json::to_string_pretty(&payload)
        .map_err(|e| format!("serialize english vocab lookup cache failed: {e}"))?;
    fs::write(&path, content).map_err(|e| format!("write english vocab lookup cache failed: {e}"))
}

fn english_vocab_lookup_cache_key(
    book_id: &str,
    word: &str,
    context_sentence: Option<&str>,
) -> String {
    let scope = context_sentence
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(|ctx| {
            let short: String = ctx.chars().take(48).collect();
            format!("ctx:{short}")
        })
        .unwrap_or_else(|| "word".to_string());
    format!("{}:{}:{}", book_id.trim(), normalize_token(word), scope)
}

fn lookup_result_is_incomplete(result: &EnglishVocabLookupResult) -> bool {
    let meaning = result.meaning_cn.trim();
    let source = result.source.trim().to_ascii_lowercase();
    let model = result.model.trim().to_ascii_lowercase();
    let example_cn = result.example_cn.trim();
    meaning.is_empty()
        || result.source == "local_fallback"
        || result.source == "local_context_fallback"
        || source.contains("local_scene")
        || source.contains("local_service_fallback")
        || source.contains("example_not_ready")
        || source.contains("example_translation_not_ready")
        || model.contains("local_scene")
        || lookup_example_is_placeholder(&result.example)
        || example_cn.is_empty()
        || !contains_cjk(example_cn)
        || meaning.contains("暂未收录")
        || meaning.contains("可先按例句语境记忆")
        || meaning.contains("后台会自动补全")
        || meaning.contains("no meaning")
        || meaning.contains("Model returned no meaning")
        || meaning.contains("释义生成不完整")
}

fn lookup_result_needs_large_model_fallback(result: &EnglishVocabLookupResult) -> bool {
    if lookup_result_is_incomplete(result) {
        return true;
    }
    if result.refresh_hint.as_deref() == Some("background_ai_refresh") {
        return true;
    }
    let source = result.source.trim();
    let meaning = result.meaning_cn.trim();
    if source.starts_with("local_translate") {
        return !contains_cjk(meaning) || meaning.chars().count() <= 1;
    }
    false
}

fn lookup_result_is_final_grade(result: &EnglishVocabLookupResult) -> bool {
    if lookup_result_needs_large_model_fallback(result) {
        return false;
    }
    let source = result.source.trim().to_ascii_lowercase();
    let model = result.model.trim().to_ascii_lowercase();
    if result.refresh_hint.as_deref() == Some("background_ai_refresh") {
        return false;
    }
    if source.contains("local_fast")
        || source.contains("local_ecdict")
        || source.contains("english_example_pack")
        || model == "local"
        || model.contains("local_fast")
        || model.contains("english_example_pack")
    {
        return false;
    }
    source.contains("dashscope")
        || source.contains("model_reviewed")
        || source.contains("llm_reviewed")
        || model.contains("qwen-turbo")
        || model.contains("dashscope")
}

fn contains_cjk(text: &str) -> bool {
    text.chars().any(|ch| {
        ('\u{4e00}'..='\u{9fff}').contains(&ch)
            || ('\u{3400}'..='\u{4dbf}').contains(&ch)
            || ('\u{f900}'..='\u{faff}').contains(&ch)
    })
}

fn lookup_example_is_placeholder(example: &str) -> bool {
    let text = example.trim().to_ascii_lowercase();
    text.is_empty()
        || text.contains("came up in a normal conversation")
        || text.contains("i want to learn the word")
        || text.contains("i want to remember the word")
        || text.contains("we need to learn the word")
        || text.contains("learn the word")
        || text.contains("remember the word")
        || text.contains("useful in everyday conversation")
        || text.contains("while preparing dinner")
        || text.contains("while preparing for the day")
        || text.contains("while making a simple plan")
        || text.contains("during their weekend errands")
        || text.contains("at home last night")
        || text.contains("will be refreshed")
        || text.contains("clear example for")
}

fn cache_insert_lookup_result(
    cache: &mut BTreeMap<String, EnglishVocabLookupResult>,
    book_id: &str,
    word: &str,
    context_sentence: Option<&str>,
    result: &EnglishVocabLookupResult,
) {
    if lookup_result_needs_large_model_fallback(result) {
        return;
    }
    let scoped_key = english_vocab_lookup_cache_key(book_id, word, context_sentence);
    upsert_cache_entry(cache, scoped_key, result);
    let plain_key = english_vocab_lookup_cache_key(book_id, word, None);
    upsert_cache_entry(cache, plain_key, result);
    if context_sentence.is_none() || result.source == "dashscope" {
        remember_lookup_in_vocab_service(book_id, word, result);
    }
}

fn remember_lookup_in_vocab_service(book_id: &str, word: &str, result: &EnglishVocabLookupResult) {
    let payload = json!({
        "book_id": book_id,
        "word": word,
        "phonetic": result.phonetic.as_str(),
        "part_of_speech": result.part_of_speech.as_str(),
        "meaning_cn": result.meaning_cn.as_str(),
        "example": result.example.as_str(),
        "example_cn": result.example_cn.as_str(),
        "source": result.source.as_str(),
        "model": result.model.as_str(),
    });
    let _ =
        english_vocab_service_post_if_running("/cache-card", &payload, Duration::from_millis(900));
}

fn upsert_cache_entry(
    cache: &mut BTreeMap<String, EnglishVocabLookupResult>,
    key: String,
    result: &EnglishVocabLookupResult,
) {
    match cache.get(&key) {
        None => {
            cache.insert(key, result.clone());
        }
        Some(existing) => {
            let existing_incomplete = lookup_result_needs_large_model_fallback(existing);
            let incoming_incomplete = lookup_result_needs_large_model_fallback(result);
            let existing_no_example_cn = existing.example_cn.trim().is_empty();
            let incoming_has_example_cn = !result.example_cn.trim().is_empty();
            if (existing_incomplete && !incoming_incomplete)
                || (existing_no_example_cn && incoming_has_example_cn)
            {
                cache.insert(key, result.clone());
            }
        }
    }
}

fn normalize_token(raw: &str) -> String {
    let mut token = raw
        .trim_matches(|c: char| !c.is_ascii_alphabetic() && c != '\'')
        .to_ascii_lowercase();
    if token.ends_with("'s") {
        token.truncate(token.len().saturating_sub(2));
    }
    token = token
        .trim_matches(|c: char| !c.is_ascii_alphabetic() && c != '\'')
        .to_string();
    token
}

fn extract_sentence_tokens(sentence: &str, max_tokens: usize) -> Vec<String> {
    let mut out = Vec::new();
    let mut seen = BTreeSet::new();
    let mut buf = String::new();
    let flush = |buf: &mut String, out: &mut Vec<String>, seen: &mut BTreeSet<String>| {
        if buf.is_empty() {
            return;
        }
        let token = normalize_token(buf);
        buf.clear();
        if token.len() < 2 {
            return;
        }
        if seen.insert(token.clone()) {
            out.push(token);
        }
    };
    for ch in sentence.chars() {
        if ch.is_ascii_alphabetic() || ch == '\'' {
            buf.push(ch);
        } else {
            flush(&mut buf, &mut out, &mut seen);
            if out.len() >= max_tokens {
                break;
            }
        }
    }
    if out.len() < max_tokens {
        flush(&mut buf, &mut out, &mut seen);
    }
    if out.len() > max_tokens {
        out.truncate(max_tokens);
    }
    out
}

fn read_english_vocab_state() -> Result<EnglishVocabState, String> {
    let path = english_vocab_state_path();
    let mut state = if path.is_file() {
        let content = fs::read_to_string(&path)
            .map_err(|e| format!("read english vocab state failed: {e}"))?;
        serde_json::from_str::<EnglishVocabState>(&content).unwrap_or_default()
    } else {
        EnglishVocabState::default()
    };
    state.state_path = path.display().to_string();
    Ok(state)
}

fn write_english_vocab_state(state: &EnglishVocabState) -> Result<(), String> {
    let path = english_vocab_state_path();
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|e| format!("create english vocab state dir failed: {e}"))?;
    }
    let content = serde_json::to_string_pretty(state)
        .map_err(|e| format!("serialize english vocab state failed: {e}"))?;
    fs::write(&path, content).map_err(|e| format!("write english vocab state failed: {e}"))
}

fn english_vocab_log_dir() -> PathBuf {
    jachin_home_dir().join("logs")
}

fn english_vocab_lookup_log_path() -> PathBuf {
    english_vocab_log_dir().join("english_vocab_lookup.log")
}

fn english_example_chain_log_path() -> PathBuf {
    english_vocab_log_dir().join("english_example_chain.jsonl")
}

fn sanitize_trace_stage(stage: &str) -> String {
    let cleaned: String = stage
        .chars()
        .map(|ch| {
            if ch.is_ascii_alphanumeric() || ch == '_' || ch == '-' {
                ch
            } else {
                '_'
            }
        })
        .collect();
    let trimmed = cleaned.trim_matches('_');
    if trimmed.is_empty() {
        "unknown".to_string()
    } else {
        trimmed.chars().take(80).collect()
    }
}

fn english_vocab_service_log_path() -> PathBuf {
    english_vocab_log_dir().join("english_vocab_service.log")
}

fn english_vocab_log_lock() -> &'static Mutex<()> {
    static LOCK: OnceLock<Mutex<()>> = OnceLock::new();
    LOCK.get_or_init(|| Mutex::new(()))
}

fn service_stdio_log(kind: &str) -> Result<fs::File, String> {
    let path = english_vocab_log_dir().join(format!("english_vocab_service_{kind}.log"));
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|e| format!("create english vocab log dir failed: {e}"))?;
    }
    OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .map_err(|e| format!("open english vocab service stdio log failed: {e}"))
}

fn english_vocab_trace(event: &str, detail: Value) {
    let _guard = english_vocab_log_lock().lock().ok();
    let path = english_vocab_lookup_log_path();
    if let Some(parent) = path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    let ts_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or_default();
    let row = json!({
        "ts_ms": ts_ms,
        "event": event,
        "detail": detail,
    });
    if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(file, "{}", row);
    }
}

fn english_example_chain_trace(stage: &str, detail: Value) {
    let _guard = english_vocab_log_lock().lock().ok();
    let path = english_example_chain_log_path();
    if let Some(parent) = path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    let ts_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or_default();
    let row = json!({
        "ts_ms": ts_ms,
        "layer": "tauri_command",
        "stage": stage,
        "detail": detail,
    });
    if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(file, "{}", row);
    }
}

fn jachin_home_dir() -> PathBuf {
    if let Ok(raw) = std::env::var("JACHIN_HOME") {
        let p = PathBuf::from(raw);
        if !p.as_os_str().is_empty() {
            return p;
        }
    }
    std::env::var("USERPROFILE")
        .or_else(|_| std::env::var("HOME"))
        .map(PathBuf::from)
        .unwrap_or_default()
        .join(".jachin")
}

fn extract_json_object(content: &str) -> String {
    let trimmed = content.trim();
    if trimmed.starts_with('{') && trimmed.ends_with('}') {
        return trimmed.to_string();
    }
    let start = trimmed.find('{').unwrap_or(0);
    let end = trimmed.rfind('}').map(|i| i + 1).unwrap_or(trimmed.len());
    trimmed[start..end].trim().to_string()
}

fn value_string(value: &Value, key: &str) -> Option<String> {
    value
        .get(key)
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(ToString::to_string)
}

fn first_non_empty(env: &HashMap<String, String>, keys: &[&str]) -> Option<String> {
    for key in keys {
        if let Some(v) = env.get(*key).map(|s| s.trim()).filter(|s| !s.is_empty()) {
            return Some(v.to_string());
        }
    }
    None
}

fn merged_env_values() -> HashMap<String, String> {
    let mut out = HashMap::new();
    for key in [
        "DASHSCOPE_API_KEY",
        "DASHSCOPE_API_KEY_CN",
        "DASHSCOPE_API_KEY_SEA",
        "DASHSCOPE_API_BASE",
        "DASHSCOPE_API_BASE_CN",
        "DASHSCOPE_API_BASE_SEA",
        "QWEN_API_KEY",
        "QWEN_AI_API_KEY",
        "JACHIN_ACTIVE_REGION",
        "JACHIN_ENGLISH_VOCAB_ALLOW_REMOTE",
        "JACHIN_ENGLISH_VOCAB_MODEL",
        "JACHIN_ENGLISH_VOCAB_API_BASE",
        "JACHIN_ENGLISH_VOCAB_SERVICE_URL",
        "JACHIN_ENGLISH_VOCAB_SERVICE_PORT",
        "LLM_MODEL",
    ] {
        if let Ok(v) = std::env::var(key) {
            if !v.trim().is_empty() {
                out.insert(key.to_string(), v);
            }
        }
    }
    for path in candidate_env_files() {
        for (k, v) in parse_env_file(&path) {
            out.entry(k).or_insert(v);
        }
    }
    out
}

fn candidate_env_files() -> Vec<PathBuf> {
    let mut out = Vec::new();
    if let Some(root) = crate::l3_spawn::project_root() {
        out.push(root.join(".env"));
    }
    if let Ok(cwd) = std::env::current_dir() {
        out.push(cwd.join(".env"));
    }
    if let Ok(home) = std::env::var("USERPROFILE").or_else(|_| std::env::var("HOME")) {
        out.push(PathBuf::from(home).join(".jachin").join(".env"));
    }
    out
}

fn parse_env_file(path: &PathBuf) -> Vec<(String, String)> {
    let Ok(content) = fs::read_to_string(path) else {
        return Vec::new();
    };
    content
        .lines()
        .filter_map(|line| {
            let line = line.trim().trim_start_matches('\u{feff}');
            if line.is_empty() || line.starts_with('#') {
                return None;
            }
            let (k, v) = line.split_once('=')?;
            let k = k.trim().to_string();
            let v = v.trim().trim_matches('"').trim_matches('\'').to_string();
            if k.is_empty() || v.is_empty() {
                None
            } else {
                Some((k, v))
            }
        })
        .collect()
}
