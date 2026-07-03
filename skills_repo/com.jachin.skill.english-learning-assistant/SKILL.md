---
name: English Learning Assistant
id: com.jachin.skill.english-learning-assistant
version: 1.0.1
required_mcps:
  - com.jachin.mcp.english-tutor
description: Offline English learning skill for correction, translation, word explanation, examples, and quizzes.
---

# English Learning Assistant

Use this skill when the user asks Jachin to help with English learning and the request can be handled by offline rules.

This first version must not call external LLMs. It should route to the installed `com.jachin.mcp.english-tutor` MCP and clearly say when a request exceeds the offline rule set.

## Supported Intents

1. Correction
   - Chinese triggers: "纠错", "改一下这句英文", "检查英文", "语法检查", "润色这句英文".
   - English triggers: "correct this sentence", "check my grammar", "fix this English".
   - Tool: `mcp:english_correct_sentence`.
   - Required slot: `text`.
   - Optional slot: `level`.

2. Translation
   - Chinese triggers: "翻译", "中译英", "英译中", "这个英语怎么说".
   - English triggers: "translate", "how to say ... in English/Chinese".
   - Tool: `mcp:english_translate_cn_en`.
   - Required slot: `text`.
   - Optional slot: `direction`, default `auto`.

3. Word Explanation
   - Chinese triggers: "讲一下这个单词", "解释单词", "这个词怎么用", "单词用法".
   - English triggers: "explain the word", "what does ... mean".
   - Tool: `mcp:english_explain_word`.
   - Required slot: `word`.

4. Example Sentences
   - Chinese triggers: "造句", "例句", "用...造句", "给我几个例句".
   - English triggers: "make example sentences", "give examples".
   - Tool: `mcp:english_make_examples`.
   - Required slot: `topic_or_word`.
   - Optional slots: `count`, `level`.

5. Quiz
   - Chinese triggers: "测验", "出题", "练习题", "考我一下".
   - English triggers: "quiz me", "make a quiz", "practice questions".
   - Tool: `mcp:english_quiz_generate`.
   - Required/optional slot: `topic`.
   - Optional slots: `count`, `level`.
   - If the user answers a generated question, use `mcp:english_quiz_check_answer`.

## Response Policy

- Reply in concise Chinese by default.
- Show the result first, then a short explanation.
- If the tool returns `not_found` or `partial_dictionary`, say that the offline version has limited coverage and can be upgraded later with Qwen/DashScope.
- Do not invent advanced grammar analysis that is not returned by the MCP.
- For packaged-mode verification, mention which MCP tool was used when the user asks how the answer was produced.

## Examples

User: 帮我纠错：I very like play basketball
Action: call `mcp:english_correct_sentence` with `text="I very like play basketball"`.

User: 翻译：项目进展
Action: call `mcp:english_translate_cn_en` with `text="项目进展"`.

User: 讲一下 progress
Action: call `mcp:english_explain_word` with `word="progress"`.

User: 用 workflow 造 3 个句子
Action: call `mcp:english_make_examples` with `topic_or_word="workflow"`, `count=3`.

User: 给我出 5 道项目英语测验
Action: call `mcp:english_quiz_generate` with `topic="project English"`, `count=5`.
