//! Kokoro 音素到 Token ID 映射（Kokoro-82M config.json vocab）
//!
//! 当 Tier 2 未返回 tokens 时，使用此 vocab 将 phonemes 转为 token IDs。

use std::collections::HashMap;
use std::sync::OnceLock;

static PHONEME_TO_ID: OnceLock<HashMap<char, i64>> = OnceLock::new();

fn phoneme_to_id_map() -> &'static HashMap<char, i64> {
    PHONEME_TO_ID.get_or_init(|| {
        let mut m = HashMap::new();
        // Kokoro-82M vocab (hexgrad/config.json) 常用字符
        m.insert(';', 1);
        m.insert(':', 2);
        m.insert(',', 3);
        m.insert('.', 4);
        m.insert('!', 5);
        m.insert('?', 6);
        m.insert(' ', 16);
        m.insert('A', 24);
        m.insert('I', 25);
        m.insert('O', 31);
        m.insert('Q', 33);
        m.insert('S', 35);
        m.insert('T', 36);
        m.insert('W', 39);
        m.insert('Y', 41);
        m.insert('a', 43);
        m.insert('b', 44);
        m.insert('c', 45);
        m.insert('d', 46);
        m.insert('e', 47);
        m.insert('f', 48);
        m.insert('h', 50);
        m.insert('i', 51);
        m.insert('j', 52);
        m.insert('k', 53);
        m.insert('l', 54);
        m.insert('m', 55);
        m.insert('n', 56);
        m.insert('o', 57);
        m.insert('p', 58);
        m.insert('q', 59);
        m.insert('r', 60);
        m.insert('s', 61);
        m.insert('t', 62);
        m.insert('u', 63);
        m.insert('v', 64);
        m.insert('w', 65);
        m.insert('x', 66);
        m.insert('y', 67);
        m.insert('z', 68);
        m.insert('ɑ', 69);
        m.insert('ɐ', 70);
        m.insert('ɒ', 71);
        m.insert('æ', 72);
        m.insert('ɔ', 76);
        m.insert('ɕ', 77);
        m.insert('ç', 78);
        m.insert('ð', 81);
        m.insert('ə', 83);
        m.insert('ɚ', 85);
        m.insert('ɛ', 86);
        m.insert('ɜ', 87);
        m.insert('ɡ', 92);
        m.insert('ɥ', 99);
        m.insert('ɨ', 101);
        m.insert('ɪ', 102);
        m.insert('ɯ', 110);
        m.insert('ŋ', 112);
        m.insert('ø', 116);
        m.insert('θ', 119);
        m.insert('œ', 120);
        m.insert('ɹ', 123);
        m.insert('ɾ', 125);
        m.insert('ʃ', 131);
        m.insert('ʊ', 135);
        m.insert('ʌ', 138);
        m.insert('ʒ', 147);
        m.insert('ʔ', 148);
        m.insert('ˈ', 156);
        m.insert('ˌ', 157);
        m.insert('ː', 158);
        m.insert('ʰ', 162);
        m.insert('ʲ', 164);
        m.insert('→', 171);
        m.insert('↗', 172);
        m.insert('↘', 173);
        m
    })
}


/// 将音素字符序列转为 Kokoro token IDs
/// 未知字符跳过（或可映射为 0）
pub fn phonemes_to_tokens(phonemes: &[String]) -> Vec<i64> {
    phonemes
        .iter()
        .flat_map(|s| s.chars())
        .filter_map(|c| phoneme_to_id_map().get(&c).copied())
        .collect()
}
