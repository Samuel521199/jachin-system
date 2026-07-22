import React from "react";
import { invoke } from "@tauri-apps/api/core";
import { BookOpen, Check, ChevronRight, Eye, Loader2, X } from "lucide-react";
import { localWordDefinitions } from "./wordBookDefinitions";
import { englishWordBooks, getWordMetadata, type EnglishWordBook } from "./wordBooks";

type Rating = "known" | "fuzzy" | "unknown";

type WordProgress = {
  seen: number;
  known: number;
  fuzzy: number;
  unknown: number;
  status: "new" | "learning" | "known";
  last_seen_at?: number;
  due_at?: number;
};

type LookupResult = {
  word: string;
  phonetic: string;
  part_of_speech: string;
  meaning_cn: string;
  example: string;
  example_cn: string;
  source: string;
  model: string;
  refresh_hint?: string;
};

type ProgressStore = Record<string, WordProgress>;

const LOOKUP_CACHE_KEY = "jachin.english_vocab.lookup_cache.v28";
const REMOTE_LOOKUP_UI_TIMEOUT_MS = 8000;
const TOKEN_LOOKUP_UI_TIMEOUT_MS = 6000;
const UPCOMING_MODEL_PREFETCH_COUNT = 8;
const CURRENT_CARD_RETRY_LIMIT = 20;
const pendingLookups = new Map<string, Promise<LookupResult>>();

function traceFrontend(stage: string, detail: Record<string, unknown> = {}) {
  void invoke("english_vocab_frontend_trace", {
    input: {
      stage,
      detail,
    },
  }).catch(() => {
    // Frontend trace is diagnostic only; never block the learning card on logs.
  });
}

type VocabState = {
  selected_book_id: string;
  progress: ProgressStore;
  daily: Record<string, { total: number; known: number; fuzzy: number; unknown: number }>;
  state_path: string;
};

type PrefetchResult = {
  started: boolean;
  queued: number;
  skipped_cached: number;
};

type PrefetchUiState = "idle" | "prefetching" | "ready" | "error";

type LocalDefinition = {
  meaning_cn: string;
  part_of_speech: string;
};

type ExamplePair = {
  example: string;
  example_cn: string;
};

type ExampleVariant = {
  id: string;
  label: string;
  example: string;
  example_cn: string;
};

const extraLocalDefinitions: Record<string, LocalDefinition> = {
  a: { meaning_cn: "\u4e00\u4e2a\uff1b\u4e00\u4ef6", part_of_speech: "art." },
  about: { meaning_cn: "\u5173\u4e8e\uff1b\u5927\u7ea6\uff1b\u5728\u9644\u8fd1", part_of_speech: "prep./adv." },
  after: { meaning_cn: "\u5728\u2026\u4e4b\u540e\uff1b\u4e4b\u540e", part_of_speech: "prep./adv." },
  an: { meaning_cn: "\u4e00\u4e2a\uff1b\u4e00\u4ef6", part_of_speech: "art." },
  and: { meaning_cn: "\u548c\uff1b\u5e76\u4e14", part_of_speech: "conj." },
  are: { meaning_cn: "\u662f\uff1b\u5904\u4e8e\uff08be \u7684\u590d\u6570/\u7b2c\u4e8c\u4eba\u79f0\u5f62\u5f0f\uff09", part_of_speech: "v." },
  at: { meaning_cn: "\u5728\uff1b\u5411\uff1b\u4ee5\u2026\u72b6\u6001", part_of_speech: "prep." },
  before: { meaning_cn: "\u5728\u2026\u4e4b\u524d", part_of_speech: "prep." },
  better: { meaning_cn: "\u66f4\u597d\u7684\uff1b\u66f4\u597d\u5730", part_of_speech: "adj./adv." },
  book: { meaning_cn: "\u4e66\uff1b\u4e66\u7c4d\uff1b\u9884\u8ba2", part_of_speech: "n./v." },
  bought: { meaning_cn: "buy \u7684\u8fc7\u53bb\u5f0f\uff1b\u4e70\uff1b\u8d2d\u4e70", part_of_speech: "v." },
  borrow: { meaning_cn: "\u501f\uff1b\u501f\u7528", part_of_speech: "v." },
  breakfast: { meaning_cn: "\u65e9\u9910\uff1b\u65e9\u996d", part_of_speech: "n." },
  budget: { meaning_cn: "\u9884\u7b97\uff1b\u9884\u7b97\u6848\uff1b\u628a\u2026\u7f16\u5165\u9884\u7b97", part_of_speech: "n./v." },
  by: { meaning_cn: "\u7531\uff1b\u9760\uff1b\u901a\u8fc7\uff1b\u5728\u2026\u65c1\u8fb9", part_of_speech: "prep." },
  can: { meaning_cn: "\u80fd\uff1b\u53ef\u4ee5\uff1b\u4f1a", part_of_speech: "modal v." },
  carry: { meaning_cn: "\u642c\uff1b\u643a\u5e26\uff1b\u8fd0\u9001\uff1b\u627f\u62c5", part_of_speech: "v." },
  chair: { meaning_cn: "\u6905\u5b50\uff1b\u4e3b\u6301", part_of_speech: "n./v." },
  charger: { meaning_cn: "\u5145\u7535\u5668", part_of_speech: "n." },
  check: { meaning_cn: "\u68c0\u67e5\uff1b\u67e5\u770b\uff1b\u6838\u5bf9", part_of_speech: "v./n." },
  checked: { meaning_cn: "checked \u662f check \u7684\u8fc7\u53bb\u5f0f\uff1b\u68c0\u67e5\uff1b\u6838\u5bf9", part_of_speech: "v." },
  comfortable: { meaning_cn: "\u8212\u9002\u7684\uff1b\u81ea\u5728\u7684", part_of_speech: "adj." },
  commute: { meaning_cn: "\u901a\u52e4\uff1b\u4e0a\u4e0b\u73ed\u8def\u7a0b", part_of_speech: "n./v." },
  cook: { meaning_cn: "\u505a\u996d\uff1b\u70f9\u996a\uff1b\u53a8\u5e08", part_of_speech: "v./n." },
  cooked: { meaning_cn: "cook \u7684\u8fc7\u53bb\u5f0f\uff1b\u505a\u996d\uff1b\u70f9\u996a", part_of_speech: "v." },
  developer: { meaning_cn: "\u5f00\u53d1\u8005\uff1b\u7a0b\u5e8f\u5458", part_of_speech: "n." },
  dinner: { meaning_cn: "\u665a\u9910\uff1b\u6b63\u9910\uff1b\u5bb4\u4f1a", part_of_speech: "n." },
  do: { meaning_cn: "\u505a\uff1b\u6267\u884c\uff1b\u5904\u7406", part_of_speech: "v." },
  doctor: { meaning_cn: "\u533b\u751f\uff1b\u535a\u58eb", part_of_speech: "n." },
  during: { meaning_cn: "\u5728\u2026\u671f\u95f4", part_of_speech: "prep." },
  every: { meaning_cn: "\u6bcf\u4e2a\uff1b\u6bcf\u4e00", part_of_speech: "det." },
  exercise: { meaning_cn: "\u953b\u70bc\uff1b\u7ec3\u4e60\uff1b\u8fd0\u52a8", part_of_speech: "n./v." },
  faster: { meaning_cn: "fast \u7684\u6bd4\u8f83\u7ea7\uff1b\u66f4\u5feb\u7684\uff1b\u66f4\u5feb\u5730", part_of_speech: "adj./adv." },
  finish: { meaning_cn: "\u5b8c\u6210\uff1b\u7ed3\u675f", part_of_speech: "v./n." },
  explained: { meaning_cn: "explained \u662f explain \u7684\u8fc7\u53bb\u5f0f\uff1b\u89e3\u91ca\uff1b\u8bf4\u660e", part_of_speech: "v." },
  for: { meaning_cn: "\u4e3a\u4e86\uff1b\u7ed9\uff1b\u5bf9\u4e8e", part_of_speech: "prep." },
  forty: { meaning_cn: "\u56db\u5341", part_of_speech: "num." },
  friday: { meaning_cn: "\u661f\u671f\u4e94", part_of_speech: "n." },
  groceries: { meaning_cn: "\u98df\u54c1\u6742\u8d27\uff1bgrocery \u7684\u590d\u6570", part_of_speech: "n." },
  grocery: { meaning_cn: "\u98df\u54c1\u6742\u8d27\uff1b\u6742\u8d27\u5e97", part_of_speech: "n." },
  grab: { meaning_cn: "\u6293\u4f4f\uff1b\u62ff\u8d77\uff1b\u8d76\u4e0a\uff1b\u62a2\u5230", part_of_speech: "v." },
  grabbed: { meaning_cn: "grab \u7684\u8fc7\u53bb\u5f0f\uff1b\u6293\u4f4f\uff1b\u62ff\u8d77\uff1b\u8d76\u4e0a", part_of_speech: "v." },
  grabbing: { meaning_cn: "grab \u7684\u73b0\u5728\u5206\u8bcd\uff1b\u6293\u4f4f\uff1b\u62ff\u8d77", part_of_speech: "v." },
  have: { meaning_cn: "\u6709\uff1b\u5403\uff1b\u8fdb\u884c\uff1b\u7ecf\u5386", part_of_speech: "v." },
  he: { meaning_cn: "\u4ed6", part_of_speech: "pron." },
  help: { meaning_cn: "\u5e2e\u52a9\uff1b\u63f4\u52a9\uff1b\u6709\u5e2e\u52a9", part_of_speech: "v./n." },
  helped: { meaning_cn: "help \u7684\u8fc7\u53bb\u5f0f\uff1b\u5e2e\u52a9\uff1b\u63f4\u52a9", part_of_speech: "v." },
  her: { meaning_cn: "\u5979\uff1b\u5979\u7684", part_of_speech: "pron./det." },
  hour: { meaning_cn: "\u5c0f\u65f6\uff1b\u65f6\u95f4", part_of_speech: "n." },
  i: { meaning_cn: "\u6211", part_of_speech: "pron." },
  important: { meaning_cn: "\u91cd\u8981\u7684\uff1b\u6709\u5f71\u54cd\u7684", part_of_speech: "adj." },
  in: { meaning_cn: "\u5728\u2026\u91cc\uff1b\u5904\u4e8e\u2026\u4e2d", part_of_speech: "prep." },
  is: { meaning_cn: "\u662f\uff1b\u5904\u4e8e", part_of_speech: "v." },
  it: { meaning_cn: "\u5b83\uff1b\u8fd9\u4ef6\u4e8b", part_of_speech: "pron." },
  keys: { meaning_cn: "key \u7684\u590d\u6570\uff1b\u94a5\u5319\uff1b\u5173\u952e", part_of_speech: "n." },
  kitchen: { meaning_cn: "\u53a8\u623f", part_of_speech: "n." },
  laundry: { meaning_cn: "\u8981\u6d17\u7684\u8863\u7269\uff1b\u6d17\u8863\u7269", part_of_speech: "n." },
  leaving: { meaning_cn: "leave \u7684\u73b0\u5728\u5206\u8bcd\uff1b\u79bb\u5f00\uff1b\u51fa\u53d1", part_of_speech: "v." },
  let: { meaning_cn: "\u8ba9\uff1b\u5141\u8bb8", part_of_speech: "v." },
  long: { meaning_cn: "\u957f\u7684\uff1b\u957f\u65f6\u95f4\u5730", part_of_speech: "adj./adv." },
  looks: { meaning_cn: "look \u7684\u7b2c\u4e09\u4eba\u79f0\u5355\u6570\uff1b\u770b\u8d77\u6765\uff1b\u770b", part_of_speech: "v." },
  lunch: { meaning_cn: "\u5348\u9910\uff1b\u5348\u996d", part_of_speech: "n." },
  made: { meaning_cn: "make \u7684\u8fc7\u53bb\u5f0f\uff1b\u505a\uff1b\u5236\u4f5c", part_of_speech: "v." },
  meals: { meaning_cn: "meal \u7684\u590d\u6570\uff1b\u9910\uff1b\u4e00\u987f\u996d", part_of_speech: "n." },
  medicine: { meaning_cn: "\u836f\uff1b\u836f\u7269\uff1b\u533b\u5b66", part_of_speech: "n." },
  meeting: { meaning_cn: "\u4f1a\u8bae\uff1b\u89c1\u9762", part_of_speech: "n." },
  message: { meaning_cn: "\u6d88\u606f\uff1b\u4fe1\u606f\uff1b\u53d1\u6d88\u606f", part_of_speech: "n./v." },
  messages: { meaning_cn: "message \u7684\u590d\u6570\uff1b\u6d88\u606f\uff1b\u4fe1\u606f", part_of_speech: "n." },
  minutes: { meaning_cn: "\u5206\u949f\uff1bminute \u7684\u590d\u6570", part_of_speech: "n." },
  morning: { meaning_cn: "\u65e9\u6668\uff1b\u4e0a\u5348", part_of_speech: "n." },
  my: { meaning_cn: "\u6211\u7684", part_of_speech: "det." },
  near: { meaning_cn: "\u5728\u9644\u8fd1\uff1b\u63a5\u8fd1", part_of_speech: "prep./adv." },
  need: { meaning_cn: "\u9700\u8981\uff1b\u5fc5\u9700", part_of_speech: "v./n." },
  neighbor: { meaning_cn: "\u90bb\u5c45\uff1b\u9644\u8fd1\u7684\u4eba", part_of_speech: "n." },
  oclock: { meaning_cn: "\u2026\u70b9\u949f", part_of_speech: "adv." },
  office: { meaning_cn: "\u529e\u516c\u5ba4\uff1b\u529e\u4e8b\u5904", part_of_speech: "n." },
  on: { meaning_cn: "\u5728\u2026\u4e0a\uff1b\u5728\u2026\u65f6\u5019\uff1b\u5173\u4e8e", part_of_speech: "prep." },
  our: { meaning_cn: "\u6211\u4eec\u7684", part_of_speech: "det." },
  package: { meaning_cn: "\u5305\u88f9\uff1b\u5305\u88c5\uff1b\u8f6f\u4ef6\u5305\uff1b\u4e00\u63fd\u5b50\u65b9\u6848", part_of_speech: "n./v." },
  parents: { meaning_cn: "parent \u7684\u590d\u6570\uff1b\u7236\u6bcd", part_of_speech: "n." },
  pay: { meaning_cn: "\u652f\u4ed8\uff1b\u4ed8\u94b1", part_of_speech: "v." },
  payment: { meaning_cn: "\u4ed8\u6b3e\uff1b\u652f\u4ed8\uff1b\u6b3e\u9879", part_of_speech: "n." },
  plan: { meaning_cn: "\u8ba1\u5212\uff1b\u6253\u7b97\uff1b\u65b9\u6848", part_of_speech: "n./v." },
  planning: { meaning_cn: "plan \u7684\u73b0\u5728\u5206\u8bcd\uff1b\u8ba1\u5212\uff1b\u89c4\u5212", part_of_speech: "v./n." },
  please: { meaning_cn: "\u8bf7\uff1b\u4f7f\u6ee1\u610f", part_of_speech: "adv./v." },
  receipt: { meaning_cn: "\u6536\u636e\uff1b\u6536\u5230", part_of_speech: "n." },
  reading: { meaning_cn: "\u9605\u8bfb\uff1bread \u7684\u73b0\u5728\u5206\u8bcd", part_of_speech: "n./v." },
  regular: { meaning_cn: "\u89c4\u5f8b\u7684\uff1b\u5b9a\u671f\u7684\uff1b\u666e\u901a\u7684", part_of_speech: "adj." },
  reviewed: { meaning_cn: "review \u7684\u8fc7\u53bb\u5f0f\uff1b\u5ba1\u67e5\uff1b\u56de\u987e", part_of_speech: "v." },
  review: { meaning_cn: "\u5ba1\u67e5\uff1b\u590d\u76d8\uff1b\u56de\u987e\uff1b\u8bc4\u8bba", part_of_speech: "v./n." },
  return: { meaning_cn: "\u5f52\u8fd8\uff1b\u8fd4\u56de\uff1b\u56de\u62a5", part_of_speech: "v./n." },
  rush: { meaning_cn: "\u5306\u5fd9\uff1b\u9ad8\u5cf0\uff1b\u51b2", part_of_speech: "n./v." },
  saturday: { meaning_cn: "\u661f\u671f\u516d", part_of_speech: "n." },
  schedule: { meaning_cn: "\u65e5\u7a0b\uff1b\u8ba1\u5212\u8868\uff1b\u5b89\u6392", part_of_speech: "n./v." },
  send: { meaning_cn: "\u53d1\u9001\uff1b\u5bc4\u51fa\uff1b\u6d3e\u9063", part_of_speech: "v." },
  she: { meaning_cn: "\u5979", part_of_speech: "pron." },
  sleep: { meaning_cn: "\u7761\u89c9\uff1b\u7761\u7720", part_of_speech: "v./n." },
  so: { meaning_cn: "\u6240\u4ee5\uff1b\u5982\u6b64\uff1b\u8fd9\u4e48", part_of_speech: "adv./conj." },
  support: { meaning_cn: "\u652f\u6301\uff1b\u652f\u6491\uff1b\u5e2e\u52a9", part_of_speech: "v./n." },
  system: { meaning_cn: "\u7cfb\u7edf\uff1b\u4f53\u7cfb\uff1b\u5236\u5ea6", part_of_speech: "n." },
  subway: { meaning_cn: "\u5730\u94c1", part_of_speech: "n." },
  successfully: { meaning_cn: "\u6210\u529f\u5730\uff1b\u987a\u5229\u5730", part_of_speech: "adv." },
  sunday: { meaning_cn: "\u661f\u671f\u65e5", part_of_speech: "n." },
  sunny: { meaning_cn: "\u6674\u6717\u7684\uff1b\u9633\u5149\u5145\u8db3\u7684", part_of_speech: "adj." },
  table: { meaning_cn: "\u684c\u5b50\uff1b\u8868\u683c", part_of_speech: "n." },
  take: { meaning_cn: "\u62ff\uff1b\u5e26\uff1b\u82b1\u8d39\uff1b\u670d\u7528", part_of_speech: "v." },
  takes: { meaning_cn: "take \u7684\u7b2c\u4e09\u4eba\u79f0\u5355\u6570\uff1b\u9700\u8981\uff1b\u82b1\u8d39\uff1b\u4e58\u5750", part_of_speech: "v." },
  teach: { meaning_cn: "\u6559\uff1b\u6559\u6388\uff1b\u4f7f\u660e\u767d", part_of_speech: "v." },
  ten: { meaning_cn: "\u5341", part_of_speech: "num." },
  the: { meaning_cn: "\u8fd9\u4e2a\uff1b\u90a3\u4e2a\uff1b\u7528\u4e8e\u7279\u6307", part_of_speech: "art." },
  there: { meaning_cn: "\u90a3\u91cc\uff1b\u5728\u90a3\u91cc", part_of_speech: "adv." },
  they: { meaning_cn: "\u4ed6\u4eec\uff1b\u5979\u4eec\uff1b\u5b83\u4eec", part_of_speech: "pron." },
  this: { meaning_cn: "\u8fd9\u4e2a\uff1b\u8fd9\u4ef6\u4e8b", part_of_speech: "pron." },
  three: { meaning_cn: "\u4e09", part_of_speech: "num." },
  through: { meaning_cn: "\u901a\u8fc7\uff1b\u7a7f\u8fc7\uff1b\u5b8c\u6210", part_of_speech: "prep./adv." },
  their: { meaning_cn: "\u4ed6\u4eec\u7684\uff1b\u5979\u4eec\u7684\uff1b\u5b83\u4eec\u7684", part_of_speech: "det." },
  them: { meaning_cn: "\u4ed6\u4eec\uff1b\u5979\u4eec\uff1b\u5b83\u4eec", part_of_speech: "pron." },
  noticed: { meaning_cn: "notice \u7684\u8fc7\u53bb\u5f0f\uff1b\u6ce8\u610f\u5230\uff1b\u7559\u610f\u5230", part_of_speech: "v." },
  preparing: { meaning_cn: "prepare \u7684\u73b0\u5728\u5206\u8bcd\uff1b\u51c6\u5907\uff1b\u9884\u5907", part_of_speech: "v." },
  talked: { meaning_cn: "talk \u7684\u8fc7\u53bb\u5f0f\uff1b\u8c08\u8bba\uff1b\u4ea4\u8c08", part_of_speech: "v." },
  last: { meaning_cn: "\u6700\u8fd1\u7684\uff1b\u4e0a\u4e00\u4e2a\uff1b\u6301\u7eed", part_of_speech: "adj./v." },
  night: { meaning_cn: "\u591c\u665a\uff1b\u665a\u4e0a", part_of_speech: "n." },
  simple: { meaning_cn: "\u7b80\u5355\u7684\uff1b\u6613\u61c2\u7684", part_of_speech: "adj." },
  found: { meaning_cn: "find \u7684\u8fc7\u53bb\u5f0f/\u8fc7\u53bb\u5206\u8bcd\uff1b\u627e\u5230\uff1b\u53d1\u73b0", part_of_speech: "v." },
  professor: { meaning_cn: "\u6559\u6388", part_of_speech: "n." },
  mentioned: { meaning_cn: "mention \u7684\u8fc7\u53bb\u5f0f\uff1b\u63d0\u5230\uff1b\u8bf4\u8d77", part_of_speech: "v." },
  lecture: { meaning_cn: "\u8bb2\u5ea7\uff1b\u8bfe\u5802\u6388\u8bfe", part_of_speech: "n." },
  students: { meaning_cn: "student \u7684\u590d\u6570\uff1b\u5b66\u751f", part_of_speech: "n." },
  compared: { meaning_cn: "compare \u7684\u8fc7\u53bb\u5f0f\uff1b\u6bd4\u8f83\uff1b\u5bf9\u7167", part_of_speech: "v." },
  class: { meaning_cn: "\u73ed\u7ea7\uff1b\u8bfe\u5802\uff1b\u7c7b\u522b", part_of_speech: "n." },
  discussion: { meaning_cn: "\u8ba8\u8bba\uff1b\u5546\u8bae", part_of_speech: "n." },
  assignment: { meaning_cn: "\u4f5c\u4e1a\uff1b\u4efb\u52a1\uff1b\u5206\u914d", part_of_speech: "n." },
  article: { meaning_cn: "\u6587\u7ae0\uff1b\u7269\u54c1\uff1b\u51a0\u8bcd", part_of_speech: "n." },
  main: { meaning_cn: "\u4e3b\u8981\u7684\uff1b\u6700\u91cd\u8981\u7684", part_of_speech: "adj." },
  argument: { meaning_cn: "\u8bba\u70b9\uff1b\u8bba\u636e\uff1b\u4e89\u8bba", part_of_speech: "n." },
  today: { meaning_cn: "\u4eca\u5929", part_of_speech: "n./adv." },
  to: { meaning_cn: "\u5230\uff1b\u5411\uff1b\u7528\u4e8e\u52a8\u8bcd\u4e0d\u5b9a\u5f0f", part_of_speech: "prep." },
  together: { meaning_cn: "\u4e00\u8d77\uff1b\u5171\u540c", part_of_speech: "adv." },
  trip: { meaning_cn: "\u65c5\u884c\uff1b\u51fa\u884c", part_of_speech: "n." },
  up: { meaning_cn: "\u5411\u4e0a\uff1b\u8d77\u6765\uff1b\u51fa\u73b0", part_of_speech: "adv./prep." },
  upstairs: { meaning_cn: "\u697c\u4e0a\uff1b\u5728\u697c\u4e0a\uff1b\u5411\u697c\u4e0a", part_of_speech: "adv./n." },
  use: { meaning_cn: "\u4f7f\u7528\uff1b\u7528\u9014", part_of_speech: "v./n." },
  uses: { meaning_cn: "use \u7684\u7b2c\u4e09\u4eba\u79f0\u5355\u6570\uff1b\u4f7f\u7528", part_of_speech: "v." },
  us: { meaning_cn: "\u6211\u4eec\uff1b\u7ed9\u6211\u4eec", part_of_speech: "pron." },
  usually: { meaning_cn: "\u901a\u5e38\uff1b\u5e73\u5e38", part_of_speech: "adv." },
  visit: { meaning_cn: "\u53c2\u89c2\uff1b\u62dc\u8bbf\uff1b\u770b\u671b", part_of_speech: "v./n." },
  walk: { meaning_cn: "\u8d70\u8def\uff1b\u6b65\u884c\uff1b\u6563\u6b65", part_of_speech: "v./n." },
  we: { meaning_cn: "\u6211\u4eec", part_of_speech: "pron." },
  weather: { meaning_cn: "\u5929\u6c14\uff1b\u6c14\u8c61", part_of_speech: "n." },
  weekend: { meaning_cn: "\u5468\u672b", part_of_speech: "n." },
  errand: { meaning_cn: "\u5dee\u4e8b\uff1b\u5916\u51fa\u529e\u4e8b\uff1b\u8dd1\u817f", part_of_speech: "n." },
  errands: { meaning_cn: "\u5916\u51fa\u529e\u7684\u6742\u4e8b\uff1b\u8dd1\u817f\u4e8b", part_of_speech: "n." },
  chicken: { meaning_cn: "\u9e21\uff1b\u9e21\u8089\uff1b\u5c0f\u9e21", part_of_speech: "n." },
  when: { meaning_cn: "\u5f53\u2026\u65f6\uff1b\u4ec0\u4e48\u65f6\u5019", part_of_speech: "conj./adv." },
  work: { meaning_cn: "\u5de5\u4f5c\uff1b\u52b3\u52a8\uff1b\u8fd0\u884c", part_of_speech: "n./v." },
  word: { meaning_cn: "\u5355\u8bcd\uff1b\u8bcd\u8bed", part_of_speech: "n." },
  write: { meaning_cn: "\u5199\uff1b\u4e66\u5199\uff1b\u64b0\u5199", part_of_speech: "v." },
  wrote: { meaning_cn: "write \u7684\u8fc7\u53bb\u5f0f\uff1b\u5199\uff1b\u64b0\u5199", part_of_speech: "v." },
  you: { meaning_cn: "\u4f60\uff1b\u4f60\u4eec", part_of_speech: "pron." },
  your: { meaning_cn: "\u4f60\u7684\uff1b\u4f60\u4eec\u7684", part_of_speech: "det." },
};

const irregularVariants: Record<string, { base: string; relation: string }> = {
  arose: { base: "arise", relation: "\u8fc7\u53bb\u5f0f" },
  arisen: { base: "arise", relation: "\u8fc7\u53bb\u5206\u8bcd" },
  ate: { base: "eat", relation: "\u8fc7\u53bb\u5f0f" },
  beaten: { base: "beat", relation: "\u8fc7\u53bb\u5206\u8bcd" },
  became: { base: "become", relation: "\u8fc7\u53bb\u5f0f" },
  begun: { base: "begin", relation: "\u8fc7\u53bb\u5206\u8bcd" },
  began: { base: "begin", relation: "\u8fc7\u53bb\u5f0f" },
  brought: { base: "bring", relation: "\u8fc7\u53bb\u5f0f/\u8fc7\u53bb\u5206\u8bcd" },
  bought: { base: "buy", relation: "\u8fc7\u53bb\u5f0f/\u8fc7\u53bb\u5206\u8bcd" },
  built: { base: "build", relation: "\u8fc7\u53bb\u5f0f/\u8fc7\u53bb\u5206\u8bcd" },
  came: { base: "come", relation: "\u8fc7\u53bb\u5f0f" },
  caught: { base: "catch", relation: "\u8fc7\u53bb\u5f0f/\u8fc7\u53bb\u5206\u8bcd" },
  chose: { base: "choose", relation: "\u8fc7\u53bb\u5f0f" },
  chosen: { base: "choose", relation: "\u8fc7\u53bb\u5206\u8bcd" },
  done: { base: "do", relation: "\u8fc7\u53bb\u5206\u8bcd" },
  did: { base: "do", relation: "\u8fc7\u53bb\u5f0f" },
  driven: { base: "drive", relation: "\u8fc7\u53bb\u5206\u8bcd" },
  drove: { base: "drive", relation: "\u8fc7\u53bb\u5f0f" },
  eaten: { base: "eat", relation: "\u8fc7\u53bb\u5206\u8bcd" },
  fallen: { base: "fall", relation: "\u8fc7\u53bb\u5206\u8bcd" },
  fell: { base: "fall", relation: "\u8fc7\u53bb\u5f0f" },
  felt: { base: "feel", relation: "\u8fc7\u53bb\u5f0f/\u8fc7\u53bb\u5206\u8bcd" },
  found: { base: "find", relation: "\u8fc7\u53bb\u5f0f/\u8fc7\u53bb\u5206\u8bcd" },
  flew: { base: "fly", relation: "\u8fc7\u53bb\u5f0f" },
  flown: { base: "fly", relation: "\u8fc7\u53bb\u5206\u8bcd" },
  forgotten: { base: "forget", relation: "\u8fc7\u53bb\u5206\u8bcd" },
  forgot: { base: "forget", relation: "\u8fc7\u53bb\u5f0f" },
  gave: { base: "give", relation: "\u8fc7\u53bb\u5f0f" },
  given: { base: "give", relation: "\u8fc7\u53bb\u5206\u8bcd" },
  gone: { base: "go", relation: "\u8fc7\u53bb\u5206\u8bcd" },
  grabbed: { base: "grab", relation: "\u8fc7\u53bb\u5f0f/\u8fc7\u53bb\u5206\u8bcd" },
  grabbing: { base: "grab", relation: "\u73b0\u5728\u5206\u8bcd" },
  grabs: { base: "grab", relation: "\u7b2c\u4e09\u4eba\u79f0\u5355\u6570" },
  went: { base: "go", relation: "\u8fc7\u53bb\u5f0f" },
  grown: { base: "grow", relation: "\u8fc7\u53bb\u5206\u8bcd" },
  grew: { base: "grow", relation: "\u8fc7\u53bb\u5f0f" },
  had: { base: "have", relation: "\u8fc7\u53bb\u5f0f/\u8fc7\u53bb\u5206\u8bcd" },
  heard: { base: "hear", relation: "\u8fc7\u53bb\u5f0f/\u8fc7\u53bb\u5206\u8bcd" },
  held: { base: "hold", relation: "\u8fc7\u53bb\u5f0f/\u8fc7\u53bb\u5206\u8bcd" },
  kept: { base: "keep", relation: "\u8fc7\u53bb\u5f0f/\u8fc7\u53bb\u5206\u8bcd" },
  known: { base: "know", relation: "\u8fc7\u53bb\u5206\u8bcd" },
  knew: { base: "know", relation: "\u8fc7\u53bb\u5f0f" },
  left: { base: "leave", relation: "\u8fc7\u53bb\u5f0f/\u8fc7\u53bb\u5206\u8bcd" },
  lost: { base: "lose", relation: "\u8fc7\u53bb\u5f0f/\u8fc7\u53bb\u5206\u8bcd" },
  made: { base: "make", relation: "\u8fc7\u53bb\u5f0f/\u8fc7\u53bb\u5206\u8bcd" },
  meant: { base: "mean", relation: "\u8fc7\u53bb\u5f0f/\u8fc7\u53bb\u5206\u8bcd" },
  met: { base: "meet", relation: "\u8fc7\u53bb\u5f0f/\u8fc7\u53bb\u5206\u8bcd" },
  paid: { base: "pay", relation: "\u8fc7\u53bb\u5f0f/\u8fc7\u53bb\u5206\u8bcd" },
  put: { base: "put", relation: "\u539f\u5f62/\u8fc7\u53bb\u5f0f/\u8fc7\u53bb\u5206\u8bcd" },
  read: { base: "read", relation: "\u539f\u5f62/\u8fc7\u53bb\u5f0f/\u8fc7\u53bb\u5206\u8bcd" },
  ran: { base: "run", relation: "\u8fc7\u53bb\u5f0f" },
  run: { base: "run", relation: "\u539f\u5f62/\u8fc7\u53bb\u5206\u8bcd" },
  said: { base: "say", relation: "\u8fc7\u53bb\u5f0f/\u8fc7\u53bb\u5206\u8bcd" },
  saw: { base: "see", relation: "\u8fc7\u53bb\u5f0f" },
  seen: { base: "see", relation: "\u8fc7\u53bb\u5206\u8bcd" },
  sold: { base: "sell", relation: "\u8fc7\u53bb\u5f0f/\u8fc7\u53bb\u5206\u8bcd" },
  sent: { base: "send", relation: "\u8fc7\u53bb\u5f0f/\u8fc7\u53bb\u5206\u8bcd" },
  sat: { base: "sit", relation: "\u8fc7\u53bb\u5f0f/\u8fc7\u53bb\u5206\u8bcd" },
  slept: { base: "sleep", relation: "\u8fc7\u53bb\u5f0f/\u8fc7\u53bb\u5206\u8bcd" },
  spent: { base: "spend", relation: "\u8fc7\u53bb\u5f0f/\u8fc7\u53bb\u5206\u8bcd" },
  spoke: { base: "speak", relation: "\u8fc7\u53bb\u5f0f" },
  spoken: { base: "speak", relation: "\u8fc7\u53bb\u5206\u8bcd" },
  stood: { base: "stand", relation: "\u8fc7\u53bb\u5f0f/\u8fc7\u53bb\u5206\u8bcd" },
  taken: { base: "take", relation: "\u8fc7\u53bb\u5206\u8bcd" },
  taught: { base: "teach", relation: "\u8fc7\u53bb\u5f0f/\u8fc7\u53bb\u5206\u8bcd" },
  told: { base: "tell", relation: "\u8fc7\u53bb\u5f0f/\u8fc7\u53bb\u5206\u8bcd" },
  thought: { base: "think", relation: "\u8fc7\u53bb\u5f0f/\u8fc7\u53bb\u5206\u8bcd" },
  threw: { base: "throw", relation: "\u8fc7\u53bb\u5f0f" },
  thrown: { base: "throw", relation: "\u8fc7\u53bb\u5206\u8bcd" },
  understood: { base: "understand", relation: "\u8fc7\u53bb\u5f0f/\u8fc7\u53bb\u5206\u8bcd" },
  wore: { base: "wear", relation: "\u8fc7\u53bb\u5f0f" },
  worn: { base: "wear", relation: "\u8fc7\u53bb\u5206\u8bcd" },
  won: { base: "win", relation: "\u8fc7\u53bb\u5f0f/\u8fc7\u53bb\u5206\u8bcd" },
  written: { base: "write", relation: "\u8fc7\u53bb\u5206\u8bcd" },
  wrote: { base: "write", relation: "\u8fc7\u53bb\u5f0f" },
};

const coreLemmaDefinitions: Record<string, LocalDefinition> = {
  arise: { meaning_cn: "\u51fa\u73b0\uff1b\u53d1\u751f\uff1b\u8d77\u56e0\u4e8e", part_of_speech: "v." },
  beat: { meaning_cn: "\u6253\u8d25\uff1b\u6572\u6253\uff1b\u8282\u62cd", part_of_speech: "v./n." },
  become: { meaning_cn: "\u53d8\u6210\uff1b\u6210\u4e3a\uff1b\u9002\u5408", part_of_speech: "v." },
  begin: { meaning_cn: "\u5f00\u59cb\uff1b\u7740\u624b", part_of_speech: "v." },
  bring: { meaning_cn: "\u5e26\u6765\uff1b\u62ff\u6765\uff1b\u5f15\u8d77", part_of_speech: "v." },
  build: { meaning_cn: "\u5efa\u9020\uff1b\u6784\u5efa\uff1b\u589e\u5f3a", part_of_speech: "v./n." },
  buy: { meaning_cn: "\u4e70\uff1b\u8d2d\u4e70", part_of_speech: "v." },
  catch: { meaning_cn: "\u6293\u4f4f\uff1b\u63a5\u4f4f\uff1b\u8d76\u4e0a\uff1b\u7406\u89e3", part_of_speech: "v." },
  choose: { meaning_cn: "\u9009\u62e9\uff1b\u6311\u9009", part_of_speech: "v." },
  come: { meaning_cn: "\u6765\uff1b\u5230\u6765\uff1b\u51fa\u73b0", part_of_speech: "v." },
  debug: { meaning_cn: "\u8c03\u8bd5\uff1b\u6392\u67e5\u9519\u8bef", part_of_speech: "v." },
  drive: { meaning_cn: "\u9a7e\u9a76\uff1b\u63a8\u52a8\uff1b\u9a71\u4f7f", part_of_speech: "v./n." },
  eat: { meaning_cn: "\u5403\uff1b\u8fdb\u98df", part_of_speech: "v." },
  fall: { meaning_cn: "\u843d\u4e0b\uff1b\u4e0b\u964d\uff1b\u8dcc\u5012", part_of_speech: "v./n." },
  feel: { meaning_cn: "\u611f\u89c9\uff1b\u89c9\u5f97\uff1b\u89e6\u6478", part_of_speech: "v." },
  find: { meaning_cn: "\u627e\u5230\uff1b\u53d1\u73b0\uff1b\u8ba4\u4e3a", part_of_speech: "v." },
  fly: { meaning_cn: "\u98de\uff1b\u4e58\u98de\u673a\uff1b\u5feb\u901f\u79fb\u52a8", part_of_speech: "v." },
  forget: { meaning_cn: "\u5fd8\u8bb0\uff1b\u5ffd\u7565", part_of_speech: "v." },
  give: { meaning_cn: "\u7ed9\uff1b\u63d0\u4f9b\uff1b\u4ea4\u7ed9", part_of_speech: "v." },
  go: { meaning_cn: "\u53bb\uff1b\u8d70\uff1b\u8fdb\u884c", part_of_speech: "v." },
  grow: { meaning_cn: "\u751f\u957f\uff1b\u589e\u957f\uff1b\u53d8\u5f97", part_of_speech: "v." },
  hear: { meaning_cn: "\u542c\u89c1\uff1b\u542c\u8bf4\uff1b\u5ba1\u7406", part_of_speech: "v." },
  hold: { meaning_cn: "\u62ff\u7740\uff1b\u4e3e\u529e\uff1b\u4fdd\u6301", part_of_speech: "v." },
  keep: { meaning_cn: "\u4fdd\u6301\uff1b\u4fdd\u7559\uff1b\u7ee7\u7eed", part_of_speech: "v." },
  know: { meaning_cn: "\u77e5\u9053\uff1b\u4e86\u89e3\uff1b\u8ba4\u8bc6", part_of_speech: "v." },
  leave: { meaning_cn: "\u79bb\u5f00\uff1b\u7559\u4e0b\uff1b\u628a\u2026\u7559\u7ed9", part_of_speech: "v." },
  lose: { meaning_cn: "\u5931\u53bb\uff1b\u4e22\u5931\uff1b\u8f93\u6389", part_of_speech: "v." },
  make: { meaning_cn: "\u505a\uff1b\u5236\u4f5c\uff1b\u4f7f\u6210\u4e3a", part_of_speech: "v." },
  mean: { meaning_cn: "\u610f\u601d\u662f\uff1b\u610f\u5473\u7740\uff1b\u6253\u7b97", part_of_speech: "v." },
  meet: { meaning_cn: "\u89c1\u9762\uff1b\u9047\u5230\uff1b\u6ee1\u8db3", part_of_speech: "v." },
  put: { meaning_cn: "\u653e\uff1b\u5b89\u7f6e\uff1b\u8868\u8fbe", part_of_speech: "v." },
  read: { meaning_cn: "\u8bfb\uff1b\u9605\u8bfb\uff1b\u7406\u89e3", part_of_speech: "v." },
  run: { meaning_cn: "\u8dd1\uff1b\u8fd0\u884c\uff1b\u7ba1\u7406", part_of_speech: "v./n." },
  say: { meaning_cn: "\u8bf4\uff1b\u8868\u793a\uff1b\u5047\u8bbe", part_of_speech: "v." },
  see: { meaning_cn: "\u770b\u89c1\uff1b\u7406\u89e3\uff1b\u4f1a\u89c1", part_of_speech: "v." },
  sell: { meaning_cn: "\u5356\uff1b\u9500\u552e", part_of_speech: "v." },
  sit: { meaning_cn: "\u5750\uff1b\u4f4d\u4e8e\uff1b\u53c2\u52a0\u8003\u8bd5", part_of_speech: "v." },
  speak: { meaning_cn: "\u8bf4\u8bdd\uff1b\u53d1\u8a00\uff1b\u4f1a\u8bf4", part_of_speech: "v." },
  spend: { meaning_cn: "\u82b1\u8d39\uff1b\u5ea6\u8fc7", part_of_speech: "v." },
  stand: { meaning_cn: "\u7ad9\u7acb\uff1b\u627f\u53d7\uff1b\u4ee3\u8868", part_of_speech: "v./n." },
  tell: { meaning_cn: "\u544a\u8bc9\uff1b\u8bf4\u660e\uff1b\u8fa8\u522b", part_of_speech: "v." },
  think: { meaning_cn: "\u60f3\uff1b\u8ba4\u4e3a\uff1b\u601d\u8003", part_of_speech: "v." },
  throw: { meaning_cn: "\u629b\uff1b\u6254\uff1b\u4e3e\u529e", part_of_speech: "v." },
  understand: { meaning_cn: "\u7406\u89e3\uff1b\u660e\u767d\uff1b\u5f97\u77e5", part_of_speech: "v." },
  wear: { meaning_cn: "\u7a7f\uff1b\u6234\uff1b\u78e8\u635f", part_of_speech: "v." },
  win: { meaning_cn: "\u8d62\uff1b\u83b7\u80dc\uff1b\u83b7\u5f97", part_of_speech: "v./n." },
};

const UI = {
  readyHint: "\u5148\u770b\u82f1\u6587\u4f8b\u53e5\uff0c\u70b9\u51fb\u91ca\u4e49\u540e\u67e5\u770b\u4e2d\u6587\u610f\u601d\u3002",
  title: "\u82f1\u8bed\u80cc\u8bcd",
  subtitle: "\u8bcd\u4e66\u5b66\u4e60 \u00b7 \u4f8b\u53e5\u7cbe\u8bb2",
  close: "\u5173\u95ed",
  reveal: "\u91ca\u4e49",
  unknown: "\u4e0d\u8ba4\u8bc6",
  fuzzy: "\u6a21\u7cca",
  know: "\u8ba4\u8bc6",
  next: "\u4e0b\u4e00\u4e2a",
  revealOnly: "\u5df2\u663e\u793a\u5355\u8bcd\u548c\u4f8b\u53e5\u91ca\u4e49\u3002",
  knownFeedback: "\u5df2\u6807\u8bb0\u8ba4\u8bc6\uff1a\u4f1a\u62c9\u957f\u590d\u4e60\u95f4\u9694\u3002",
  fuzzyFeedback: "\u5df2\u6807\u8bb0\u6a21\u7cca\uff1a1 \u5c0f\u65f6\u540e\u4f1a\u4f18\u5148\u590d\u4e60\u3002",
  unknownFeedback: "\u5df2\u6807\u8bb0\u4e0d\u8ba4\u8bc6\uff1a\u7a0d\u540e\u4f1a\u66f4\u5feb\u51fa\u73b0\u3002",
  loading: "\u67e5\u8be2\u4e2d...",
  lookupLocal: "正在查本地词典...",
  lookupLocalHit: "已命中本地词典",
  lookupBackground: "正在补全释义和例句...",
  lookupBackgroundFailed: "补全失败，已显示可用释义",
  lookupDone: "查询完成",
  exampleLoading: "\u6b63\u5728\u751f\u6210\u9ad8\u8d28\u91cf\u4f8b\u53e5...",
  examplePending: "\u6b63\u5728\u51c6\u5907\u4f8b\u53e5...",
  preparingCard: "正在准备下一张高质量单词卡...",
  retry: "\u91cd\u8bd5",
  lookupUnavailable: "\u91ca\u4e49\u67e5\u8be2\u6682\u65f6\u4e0d\u53ef\u7528\uff0c\u8bf7\u70b9\u51fb\u91cd\u8bd5\u3002",
  prefetching: "\u8bcd\u4e49\u9884\u53d6\u4e2d",
  prefetchReady: "\u8bcd\u4e49\u5df2\u5c31\u7eea",
  prefetchError: "\u9884\u53d6\u5f02\u5e38",
  sectionTitle: "\u4eca\u65e5\u5355\u8bcd",
  exampleTitle: "\u82f1\u6587\u4f8b\u53e5",
  meaningTitle: "\u91ca\u4e49",
  sentenceMeaningTitle: "\u4f8b\u53e5\u4e2d\u6587",
  fallbackMeaning: "\u8bcd\u4e49\u6b63\u5728\u51c6\u5907\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5\u3002",
};

function userFacingMeaning(meaning?: string | null) {
  const text = (meaning ?? "").trim();
  if (!text) return "";
  if (text.includes("\u672c\u5730\u8bcd\u5178\u6682\u672a\u6536\u5f55") || text.includes("\u672c\u5730\u8bcd\u5178\u6682\u672a\u547d\u4e2d")) {
    return UI.fallbackMeaning;
  }
  if (text.includes("\u6a21\u578b\u6b63\u5728\u8865\u5168") || text.includes("\u8865\u5168\u8be5\u8bcd\u91ca\u4e49")) {
    return UI.fallbackMeaning;
  }
  return text;
}

function getBookById(id?: string | null): EnglishWordBook {
  return englishWordBooks.find((book) => book.id === id) ?? englishWordBooks[0];
}

function progressKey(bookId: string, word: string) {
  return `${bookId}:${word.toLowerCase()}`;
}

function chooseWord(words: string[], progress: ProgressStore, bookId: string, currentWord?: string): string {
  const now = Date.now();
  const scored = words
    .filter((word) => word !== currentWord)
    .map((word, index) => {
      const p = progress[progressKey(bookId, word)];
      if (!p) return { word, score: 100000 - index };
      const dueBonus = !p.due_at || p.due_at <= now ? 60000 : -Math.min(30000, p.due_at - now);
      const learningBonus = p.status === "learning" ? 25000 : 0;
      const fuzzyBonus = p.fuzzy * 3500;
      const unknownBonus = p.unknown * 5500;
      const fatiguePenalty = p.known * 2500 + p.seen * 800;
      return { word, score: dueBonus + learningBonus + fuzzyBonus + unknownBonus - fatiguePenalty - index };
    })
    .sort((a, b) => b.score - a.score);
  return scored[0]?.word ?? words[0] ?? "word";
}

function feedbackFor(rating: Rating) {
  if (rating === "known") return UI.knownFeedback;
  if (rating === "fuzzy") return UI.fuzzyFeedback;
  return UI.unknownFeedback;
}

function errorText(error: unknown) {
  return error instanceof Error ? error.message : String(error || "unknown error");
}

function lookupSourceLabel(result?: LookupResult | null) {
  const source = (result?.source || "").toLowerCase();
  if (!source) return UI.lookupDone;
  if (source.includes("cache")) return "查询完成：缓存命中";
  if (source.includes("dictionary") || source.includes("ecdict")) return "查询完成：本地词典";
  if (source.includes("dashscope")) return "查询完成：智能补全";
  if (source.includes("fallback")) return "已显示可用释义，后台会继续补全";
  return UI.lookupDone;
}

function cleanToken(token: string) {
  return token.toLowerCase().replace(/^[^a-z']+|[^a-z']+$/g, "");
}

type LookupCandidate = {
  word: string;
  relation?: string;
};

function lookupCandidateEntries(token: string): LookupCandidate[] {
  const word = cleanToken(token).replace(/'s$/, "");
  const candidates: LookupCandidate[] = [{ word }];
  const irregular = irregularVariants[word];
  if (irregular) candidates.push({ word: irregular.base, relation: irregular.relation });
  if (word.endsWith("ies") && word.length > 4) {
    candidates.push({ word: `${word.slice(0, -3)}y`, relation: "\u590d\u6570/\u7b2c\u4e09\u4eba\u79f0\u5355\u6570" });
  }
  if (word.endsWith("ied") && word.length > 4) {
    candidates.push({ word: `${word.slice(0, -3)}y`, relation: "\u8fc7\u53bb\u5f0f/\u8fc7\u53bb\u5206\u8bcd" });
  }
  if (word.endsWith("es") && word.length > 4) {
    candidates.push({ word: word.slice(0, -2), relation: "\u590d\u6570/\u7b2c\u4e09\u4eba\u79f0\u5355\u6570" });
  }
  if (word.endsWith("s") && word.length > 3) {
    candidates.push({ word: word.slice(0, -1), relation: "\u590d\u6570/\u7b2c\u4e09\u4eba\u79f0\u5355\u6570" });
  }
  if (word.endsWith("ed") && word.length > 4) {
    const stem = word.slice(0, -2);
    candidates.push({ word: stem, relation: "\u8fc7\u53bb\u5f0f/\u8fc7\u53bb\u5206\u8bcd" }, { word: `${stem}e`, relation: "\u8fc7\u53bb\u5f0f/\u8fc7\u53bb\u5206\u8bcd" });
    if (stem.length > 2 && stem[stem.length - 1] === stem[stem.length - 2]) {
      candidates.push({ word: stem.slice(0, -1), relation: "\u8fc7\u53bb\u5f0f/\u8fc7\u53bb\u5206\u8bcd" });
    }
  }
  if (word.endsWith("ing") && word.length > 5) {
    const stem = word.slice(0, -3);
    candidates.push({ word: stem, relation: "\u73b0\u5728\u5206\u8bcd/\u52a8\u540d\u8bcd" }, { word: `${stem}e`, relation: "\u73b0\u5728\u5206\u8bcd/\u52a8\u540d\u8bcd" });
    if (stem.length > 2 && stem[stem.length - 1] === stem[stem.length - 2]) {
      candidates.push({ word: stem.slice(0, -1), relation: "\u73b0\u5728\u5206\u8bcd/\u52a8\u540d\u8bcd" });
    }
  }
  if (word.endsWith("er") && word.length > 4) candidates.push({ word: word.slice(0, -2), relation: "\u6bd4\u8f83\u7ea7/\u884c\u4e3a\u8005\u5f62\u5f0f" });
  if (word.endsWith("est") && word.length > 5) candidates.push({ word: word.slice(0, -3), relation: "\u6700\u9ad8\u7ea7" });
  const seen = new Set<string>();
  return candidates.filter((candidate) => {
    if (!candidate.word || seen.has(candidate.word)) return false;
    seen.add(candidate.word);
    return true;
  });
}

function todayKey() {
  const d = new Date();
  const year = d.getFullYear();
  const month = `${d.getMonth() + 1}`.padStart(2, "0");
  const day = `${d.getDate()}`.padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function lookupCacheKey(bookId: string, word: string, contextSentence?: string) {
  const scope = contextSentence ? `ctx:${contextSentence.slice(0, 48)}` : "word";
  return `${bookId}:${word.toLowerCase()}:${scope}`;
}

function isPlaceholderExample(example?: string | null) {
  const text = (example ?? "").trim().toLowerCase();
  return (
    !text ||
    /\bi want to (learn|remember|memorize) the word\b/.test(text) ||
    /\bwe need to (learn|remember|memorize) the word\b/.test(text) ||
    /\blearn the word\b/.test(text) ||
    /\bremember the word\b/.test(text) ||
    /\bmemorize the word\b/.test(text) ||
    text.includes("came up in a normal conversation") ||
    text.includes("we met near the") ||
    text.includes("useful in everyday conversation") ||
    text.includes("while preparing for the day") ||
    text.includes("during their weekend errands") ||
    text.includes("at home last night") ||
    text.includes("while making a simple plan") ||
    text.includes("while preparing dinner") ||
    text.includes("clear example for") ||
    text.includes("people discussed the") ||
    text.includes("the article mentioned the") ||
    text.includes("we included the") ||
    text.includes("plays an important role in daily life") ||
    text.includes("had a discount on") ||
    text.includes("was on sale") ||
    text.includes("on the kitchen table") ||
    text.includes("before getting on the bus") ||
    text.includes("packed the ") ||
    text.startsWith("we should ") ||
    text.startsWith("this option feels ") ||
    text.includes("put the ") ||
    text.includes("will be refreshed locally")
  );
}

function isFallbackExampleSource(result?: Partial<Pick<LookupResult, "source" | "model">> | null) {
  const source = (result?.source || "").trim().toLowerCase();
  const model = (result?.model || "").trim().toLowerCase();
  return (
    source === "local_fallback" ||
    source === "local_context_fallback" ||
    source.includes("local_service_fallback") ||
    source.includes("local_scene") ||
    model.includes("local_scene_template") ||
    model.includes("local_scene_templates")
  );
}

function isContextLookupSource(result?: Partial<Pick<LookupResult, "source">> | null) {
  return (result?.source || "").trim().toLowerCase().includes("context");
}

function isTrustedModelSource(result?: Partial<Pick<LookupResult, "source" | "model">> | null) {
  const source = (result?.source || "").trim().toLowerCase();
  const model = (result?.model || "").trim().toLowerCase();
  return (
    source.includes("dashscope") ||
    source.includes("qwen_turbo") ||
    model.includes("qwen-turbo") ||
    source.includes("model_reviewed") ||
    source.includes("llm_reviewed") ||
    (source.includes("completion_cache") && (model.includes("qwen-turbo") || model.includes("dashscope")))
  );
}

function hasDisplayableExample(result?: LookupResult | null) {
  if (!result || isFallbackExampleSource(result)) return false;
  if (!result.example?.trim() || isPlaceholderExample(result.example)) return false;
  if (!result.example_cn?.trim()) return false;
  if (isTrustedModelSource(result)) return true;
  return !isSemanticallyBadExample(result.example, result.word, result.meaning_cn);
}

function choosePhonetic(...values: Array<string | undefined | null>) {
  for (const value of values) {
    const text = value?.trim();
    if (!text || text === "-") continue;
    if (/[A-Z]/.test(text)) continue;
    if (text.includes("?")) continue;
    return text;
  }
  return "-";
}

function readLookupCache(): Record<string, LookupResult> {
  try {
    const raw = localStorage.getItem(LOOKUP_CACHE_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return Object.fromEntries(
      Object.entries(parsed).filter(([, value]) => {
        const item = value as Partial<LookupResult>;
        const meaning = item.meaning_cn ?? "";
        return (
          typeof item.word === "string" &&
          typeof meaning === "string" &&
          !meaning.includes("暂未收录") &&
          !meaning.includes("\u8054\u7f51") &&
          !meaning.includes("DashScope") &&
          !meaning.toLowerCase().includes("request failed") &&
          !meaning.includes("\u67e5\u8be2\u5931\u8d25") &&
          !isPlaceholderExample(item.example) &&
          (isTrustedModelSource(item as Partial<LookupResult>) ||
            !isSemanticallyBadExample(item.example, item.word, item.meaning_cn)) &&
          !isFallbackExampleSource(item as Partial<LookupResult>) &&
          item.example_cn !== "\u5148\u7406\u89e3\u8fd9\u4e2a\u8bcd\u7684\u6838\u5fc3\u542b\u4e49\uff0c\u518d\u6839\u636e\u8bb0\u5fc6\u7a0b\u5ea6\u9009\u62e9\u3002" &&
          item.example_cn !== "\u8fd9\u4e2a\u8bcd\u53ef\u4ee5\u5148\u6309\u8bed\u5883\u8bb0\u5fc6\uff0c\u9047\u5230\u4f8b\u53e5\u65f6\u518d\u56de\u6765\u5de9\u56fa\u3002" &&
          item.source !== "local_fallback" &&
          item.source !== "local_context_fallback"
        );
      }),
    ) as Record<string, LookupResult>;
  } catch {
    return {};
  }
}

function writeLookupCache(cache: Record<string, LookupResult>) {
  try {
    localStorage.setItem(LOOKUP_CACHE_KEY, JSON.stringify(cache));
  } catch {
    // Cache is an optimization. Learning should continue even if storage is full.
  }
}

function cacheLookup(
  cacheRef: React.MutableRefObject<Record<string, LookupResult>>,
  key: string,
  result: LookupResult,
) {
  if (result.refresh_hint === "background_ai_refresh") {
    return;
  }
  const next = { ...cacheRef.current, [key]: result };
  cacheRef.current = next;
  writeLookupCache(next);
}

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new Error("lookup timeout")), ms);
    promise
      .then((value) => {
        window.clearTimeout(timer);
        resolve(value);
      })
      .catch((error) => {
        window.clearTimeout(timer);
        reject(error);
      });
  });
}

function definitionFor(word: string): LocalDefinition | undefined {
  return extraLocalDefinitions[word] ?? localWordDefinitions[word] ?? coreLemmaDefinitions[word];
}

function shortMeaning(word: string) {
  const meaning = definitionFor(word)?.meaning_cn;
  return meaning?.split(/[\uff1b;]/)[0]?.trim() || word;
}

function curatedExampleFor(word: string): ExamplePair | undefined {
  return curatedExamples[word.toLowerCase()];
}

const curatedExamples: Record<string, ExamplePair> = {
  morning: {
    example: "I usually check my messages in the morning.",
    example_cn: "\u6211\u901a\u5e38\u5728\u65e9\u4e0a\u67e5\u770b\u6d88\u606f\u3002",
  },
  breakfast: {
    example: "She made breakfast before leaving for work.",
    example_cn: "\u5979\u4e0a\u73ed\u524d\u505a\u4e86\u65e9\u9910\u3002",
  },
  bread: {
    example: "She bought fresh bread from the bakery.",
    example_cn: "\u5979\u4ece\u9762\u5305\u5e97\u4e70\u4e86\u65b0\u9c9c\u9762\u5305\u3002",
  },
  lunch: {
    example: "Let us have lunch near the office today.",
    example_cn: "\u6211\u4eec\u4eca\u5929\u5728\u529e\u516c\u5ba4\u9644\u8fd1\u5403\u5348\u9910\u5427\u3002",
  },
  office: {
    example: "She left her laptop at the office.",
    example_cn: "她把笔记本电脑落在办公室了。",
  },
  dinner: {
    example: "They cooked dinner together after work.",
    example_cn: "\u4ed6\u4eec\u4e0b\u73ed\u540e\u4e00\u8d77\u505a\u665a\u9910\u3002",
  },
  commute: {
    example: "My commute takes about forty minutes by subway.",
    example_cn: "\u6211\u4e58\u5730\u94c1\u901a\u52e4\u5927\u7ea6\u9700\u8981\u56db\u5341\u5206\u949f\u3002",
  },
  grocery: {
    example: "He bought groceries for the week on Sunday.",
    example_cn: "\u4ed6\u661f\u671f\u5929\u4e70\u4e86\u4e00\u5468\u7684\u98df\u54c1\u6742\u8d27\u3002",
  },
  chicken: {
    example: "She cooked chicken for dinner.",
    example_cn: "\u5979\u665a\u9910\u505a\u4e86\u9e21\u8089\u3002",
  },
  child: {
    example: "The child drew a picture for her teacher.",
    example_cn: "\u8fd9\u4e2a\u5b69\u5b50\u7ed9\u5979\u7684\u8001\u5e08\u753b\u4e86\u4e00\u5e45\u753b\u3002",
  },
  children: {
    example: "The children played quietly in the garden.",
    example_cn: "\u5b69\u5b50\u4eec\u5728\u82b1\u56ed\u91cc\u5b89\u9759\u5730\u73a9\u3002",
  },
  receipt: {
    example: "Please keep the receipt after you pay.",
    example_cn: "\u4ed8\u6b3e\u540e\u8bf7\u4fdd\u7559\u6536\u636e\u3002",
  },
  appointment: {
    example: "I have a doctor appointment at three o'clock.",
    example_cn: "\u6211\u4e09\u70b9\u6709\u4e00\u4e2a\u533b\u751f\u9884\u7ea6\u3002",
  },
  neighbor: {
    example: "Our neighbor helped us carry the package upstairs.",
    example_cn: "\u90bb\u5c45\u5e2e\u6211\u4eec\u628a\u5305\u88f9\u642c\u4e0a\u697c\u3002",
  },
  weather: {
    example: "The weather looks sunny, so we can walk there.",
    example_cn: "\u5929\u6c14\u770b\u8d77\u6765\u5f88\u6674\u6717\uff0c\u6240\u4ee5\u6211\u4eec\u53ef\u4ee5\u8d70\u8fc7\u53bb\u3002",
  },
  medicine: {
    example: "Take this medicine after meals.",
    example_cn: "\u996d\u540e\u670d\u7528\u8fd9\u79cd\u836f\u3002",
  },
  budget: {
    example: "We need a budget before planning the trip.",
    example_cn: "\u8ba1\u5212\u65c5\u884c\u524d\u6211\u4eec\u9700\u8981\u4e00\u4e2a\u9884\u7b97\u3002",
  },
  borrow: {
    example: "Can I borrow your charger for ten minutes?",
    example_cn: "\u6211\u80fd\u501f\u4f60\u7684\u5145\u7535\u5668\u5341\u5206\u949f\u5417\uff1f",
  },
  return: {
    example: "Please return the book when you finish it.",
    example_cn: "\u770b\u5b8c\u8fd9\u672c\u4e66\u540e\u8bf7\u5f52\u8fd8\u3002",
  },
  laundry: {
    example: "I do my laundry every Saturday morning.",
    example_cn: "\u6211\u6bcf\u5468\u516d\u65e9\u4e0a\u6d17\u8863\u670d\u3002",
  },
  kitchen: {
    example: "The keys are on the kitchen table.",
    example_cn: "\u94a5\u5319\u5728\u53a8\u623f\u684c\u5b50\u4e0a\u3002",
  },
  comfortable: {
    example: "This chair is comfortable for long reading.",
    example_cn: "\u8fd9\u628a\u6905\u5b50\u9002\u5408\u957f\u65f6\u95f4\u9605\u8bfb\uff0c\u5f88\u8212\u670d\u3002",
  },
  message: {
    example: "I sent her a message after the meeting.",
    example_cn: "\u4f1a\u8bae\u7ed3\u675f\u540e\u6211\u7ed9\u5979\u53d1\u4e86\u6d88\u606f\u3002",
  },
  schedule: {
    example: "Please check your schedule before Friday.",
    example_cn: "\u8bf7\u5728\u5468\u4e94\u524d\u67e5\u770b\u4f60\u7684\u65e5\u7a0b\u3002",
  },
  weekend: {
    example: "We plan to visit our parents this weekend.",
    example_cn: "\u6211\u4eec\u8ba1\u5212\u8fd9\u4e2a\u5468\u672b\u53bb\u770b\u7236\u6bcd\u3002",
  },
  exercise: {
    example: "Regular exercise helps me sleep better.",
    example_cn: "\u89c4\u5f8b\u953b\u70bc\u80fd\u5e2e\u52a9\u6211\u7761\u5f97\u66f4\u597d\u3002",
  },
  subway: {
    example: "The subway is faster during rush hour.",
    example_cn: "\u9ad8\u5cf0\u671f\u5750\u5730\u94c1\u66f4\u5feb\u3002",
  },
  payment: {
    example: "The payment went through successfully.",
    example_cn: "\u8fd9\u7b14\u4ed8\u6b3e\u5df2\u7ecf\u6210\u529f\u5b8c\u6210\u3002",
  },
  delivery: {
    example: "The delivery will arrive this afternoon.",
    example_cn: "\u5feb\u9012\u4eca\u5929\u4e0b\u5348\u4f1a\u9001\u5230\u3002",
  },
  agenda: {
    example: "The agenda includes three project updates.",
    example_cn: "\u8bae\u7a0b\u5305\u62ec\u4e09\u4e2a\u9879\u76ee\u8fdb\u5c55\u66f4\u65b0\u3002",
  },
  deadline: {
    example: "The deadline for the report is Friday.",
    example_cn: "\u62a5\u544a\u7684\u622a\u6b62\u65e5\u671f\u662f\u5468\u4e94\u3002",
  },
  feedback: {
    example: "I need your feedback on this draft.",
    example_cn: "\u6211\u9700\u8981\u4f60\u5bf9\u8fd9\u4efd\u8349\u7a3f\u7684\u53cd\u9988\u3002",
  },
  proposal: {
    example: "The client approved our proposal this morning.",
    example_cn: "\u5ba2\u6237\u4eca\u5929\u65e9\u4e0a\u6279\u51c6\u4e86\u6211\u4eec\u7684\u65b9\u6848\u3002",
  },
  invoice: {
    example: "The finance team sent the invoice yesterday.",
    example_cn: "\u8d22\u52a1\u56e2\u961f\u6628\u5929\u53d1\u51fa\u4e86\u53d1\u7968\u3002",
  },
  workflow: {
    example: "This workflow saves the team several hours each week.",
    example_cn: "\u8fd9\u4e2a\u5de5\u4f5c\u6d41\u6bcf\u5468\u4e3a\u56e2\u961f\u8282\u7701\u51e0\u4e2a\u5c0f\u65f6\u3002",
  },
  priority: {
    example: "Security is the top priority for this release.",
    example_cn: "\u5b89\u5168\u6027\u662f\u8fd9\u6b21\u53d1\u5e03\u7684\u6700\u9ad8\u4f18\u5148\u7ea7\u3002",
  },
  milestone: {
    example: "The next milestone is scheduled for July.",
    example_cn: "\u4e0b\u4e00\u4e2a\u91cc\u7a0b\u7891\u8ba1\u5212\u5728\u4e03\u6708\u5b8c\u6210\u3002",
  },
  algorithm: {
    example: "The algorithm ranks search results by relevance.",
    example_cn: "\u8fd9\u4e2a\u7b97\u6cd5\u6309\u76f8\u5173\u6027\u6392\u5217\u641c\u7d22\u7ed3\u679c\u3002",
  },
  database: {
    example: "The database stores user preferences locally.",
    example_cn: "\u6570\u636e\u5e93\u5728\u672c\u5730\u5b58\u50a8\u7528\u6237\u504f\u597d\u3002",
  },
  frontend: {
    example: "The frontend shows the task status in real time.",
    example_cn: "\u524d\u7aef\u5b9e\u65f6\u663e\u793a\u4efb\u52a1\u72b6\u6001\u3002",
  },
  backend: {
    example: "The backend validates the request before saving it.",
    example_cn: "\u540e\u7aef\u5728\u4fdd\u5b58\u524d\u9a8c\u8bc1\u8bf7\u6c42\u3002",
  },
  deployment: {
    example: "The deployment finished without errors.",
    example_cn: "\u90e8\u7f72\u5b8c\u6210\u4e14\u6ca1\u6709\u9519\u8bef\u3002",
  },
  cache: {
    example: "The app uses a cache to load pages faster.",
    example_cn: "\u5e94\u7528\u4f7f\u7528\u7f13\u5b58\u6765\u66f4\u5feb\u52a0\u8f7d\u9875\u9762\u3002",
  },
  latency: {
    example: "Lower latency makes the assistant feel more responsive.",
    example_cn: "\u66f4\u4f4e\u7684\u5ef6\u8fdf\u4f1a\u8ba9\u52a9\u624b\u54cd\u5e94\u66f4\u5feb\u3002",
  },
  repository: {
    example: "The repository contains the latest source code.",
    example_cn: "\u4ed3\u5e93\u5305\u542b\u6700\u65b0\u7684\u6e90\u4ee3\u7801\u3002",
  },
  pipeline: {
    example: "The pipeline runs tests before publishing.",
    example_cn: "\u6d41\u6c34\u7ebf\u5728\u53d1\u5e03\u524d\u8fd0\u884c\u6d4b\u8bd5\u3002",
  },
  rollback: {
    example: "We prepared a rollback plan before the release.",
    example_cn: "\u53d1\u5e03\u524d\u6211\u4eec\u51c6\u5907\u4e86\u56de\u6eda\u65b9\u6848\u3002",
  },
  campus: {
    example: "The campus library stays open late during exams.",
    example_cn: "\u8003\u8bd5\u671f\u95f4\u6821\u56ed\u56fe\u4e66\u9986\u5f00\u653e\u5230\u5f88\u665a\u3002",
  },
  lecture: {
    example: "The lecture focused on climate change.",
    example_cn: "\u8fd9\u8282\u8bfe\u91cd\u70b9\u8bb2\u4e86\u6c14\u5019\u53d8\u5316\u3002",
  },
  research: {
    example: "Her research examines how children learn languages.",
    example_cn: "\u5979\u7684\u7814\u7a76\u63a2\u8ba8\u513f\u7ae5\u5982\u4f55\u5b66\u4e60\u8bed\u8a00\u3002",
  },
  assignment: {
    example: "The assignment is due next Monday.",
    example_cn: "\u4f5c\u4e1a\u4e0b\u5468\u4e00\u622a\u6b62\u3002",
  },
  experiment: {
    example: "The experiment tested three different methods.",
    example_cn: "\u5b9e\u9a8c\u6d4b\u8bd5\u4e86\u4e09\u79cd\u4e0d\u540c\u7684\u65b9\u6cd5\u3002",
  },
  conclusion: {
    example: "The conclusion summarizes the main evidence.",
    example_cn: "\u7ed3\u8bba\u603b\u7ed3\u4e86\u4e3b\u8981\u8bc1\u636e\u3002",
  },
};

type SceneTemplate = {
  id: string;
  example: (word: string) => string;
  example_cn: (meaning: string) => string;
};

type SceneTemplatePos = "noun" | "verb" | "adjective" | "other";

type SemanticCategory =
  | "transport"
  | "action"
  | "place"
  | "food"
  | "portable_object"
  | "work_item"
  | "tech"
  | "money"
  | "person"
  | "time"
  | "abstract"
  | "generic";

const SEMANTIC_WORDS: Record<SemanticCategory, Set<string>> = {
  transport: new Set(["airport", "bus", "car", "flight", "plane", "station", "subway", "taxi", "train"]),
  action: new Set([
    "call",
    "clean",
    "cook",
    "drive",
    "learn",
    "listen",
    "read",
    "run",
    "shop",
    "study",
    "talk",
    "travel",
    "wait",
    "walk",
    "work",
    "write",
  ]),
  place: new Set([
    "bank",
    "beach",
    "building",
    "campus",
    "city",
    "classroom",
    "country",
    "hotel",
    "hospital",
    "kitchen",
    "lab",
    "laboratory",
    "library",
    "market",
    "museum",
    "office",
    "park",
    "restaurant",
    "river",
    "road",
    "room",
    "school",
    "store",
    "street",
    "village",
  ]),
  food: new Set([
    "breakfast",
    "bread",
    "chicken",
    "coffee",
    "dinner",
    "egg",
    "fruit",
    "lunch",
    "meal",
    "milk",
    "oil",
    "rice",
    "tea",
    "vegetable",
    "water",
  ]),
  portable_object: new Set([
    "bag",
    "book",
    "charger",
    "computer",
    "key",
    "keys",
    "laptop",
    "medicine",
    "package",
    "phone",
    "receipt",
    "ticket",
    "umbrella",
    "wallet",
  ]),
  work_item: new Set([
    "agenda",
    "budget",
    "deadline",
    "document",
    "feedback",
    "invoice",
    "meeting",
    "message",
    "milestone",
    "plan",
    "priority",
    "project",
    "proposal",
    "report",
    "schedule",
    "task",
    "workflow",
  ]),
  tech: new Set([
    "algorithm",
    "api",
    "backend",
    "cache",
    "config",
    "database",
    "deployment",
    "endpoint",
    "frontend",
    "latency",
    "pipeline",
    "repository",
    "rollback",
    "server",
    "service",
    "system",
  ]),
  money: new Set(["bill", "cash", "cost", "fee", "money", "payment", "price", "salary"]),
  person: new Set(["doctor", "engineer", "friend", "manager", "neighbor", "teacher"]),
  time: new Set(["afternoon", "evening", "friday", "morning", "night", "sunday", "weekend"]),
  abstract: new Set(["choice", "conclusion", "discussion", "idea", "issue", "reason", "research", "risk", "weather"]),
  generic: new Set(),
};

function inferSemanticCategory(word: string, meaningCn = "", bookId = ""): SemanticCategory {
  const clean = cleanToken(word) || word.trim().toLowerCase();
  for (const category of Object.keys(SEMANTIC_WORDS) as SemanticCategory[]) {
    if (category !== "generic" && SEMANTIC_WORDS[category].has(clean)) return category;
  }
  const hint = `${meaningCn} ${bookId}`.toLowerCase();
  if (bookId === "computer_science" || /api|cache|server|database|deployment|latency|software|system/.test(hint)) {
    return "tech";
  }
  if (bookId === "workplace" || /meeting|project|report|budget|agenda|schedule|deadline/.test(hint)) {
    return "work_item";
  }
  if (/[旅馆酒店客栈机场车站学校医院银行办公室公园餐厅商店市场图书馆教室城市村庄]/.test(meaningCn)) {
    return "place";
  }
  if (/[早餐午餐晚餐面包鸡肉鸡蛋咖啡茶水米饭水果蔬菜食物餐]/.test(meaningCn)) {
    return "food";
  }
  if (/[消息报告计划预算议程日程截止反馈项目任务文档发票]/.test(meaningCn)) {
    return "work_item";
  }
  if (/[手机电脑书钥匙包票据收据雨伞钱包药]/.test(meaningCn)) {
    return "portable_object";
  }
  if (/[早晨上午下午晚上周末星期夜晚]/.test(meaningCn)) {
    return "time";
  }
  return "generic";
}

function isSemanticallyBadExample(example?: string | null, word?: string | null, meaningCn?: string | null) {
  const cleanWord = cleanToken(word ?? "");
  const text = (example ?? "").trim().toLowerCase();
  if (!text || !cleanWord) return false;
  const category = inferSemanticCategory(cleanWord, meaningCn ?? "");
  const wordPattern = cleanWord.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

  if (
    category === "place" &&
    (new RegExp(`\\b(left|put|packed|grabbed|bought|cooked|ate|drank)\\b[^.?!]*\\b${wordPattern}\\b`).test(text) ||
      new RegExp(`\\b${wordPattern}\\b[^.?!]*\\b(on|in) the kitchen table\\b`).test(text) ||
      text.includes(`discount on ${cleanWord}`) ||
      text.includes(`${cleanWord} was on sale`))
  ) {
    return true;
  }

  if (
    category !== "food" &&
    (new RegExp(`\\b(cooked|ate|drank|ordered)\\b[^.?!]*\\b${wordPattern}\\b`).test(text) ||
      new RegExp(`\\b${wordPattern}\\b[^.?!]*\\b(after dinner|for breakfast)\\b`).test(text))
  ) {
    return true;
  }

  if (
    category !== "portable_object" &&
    new RegExp(`\\b(grabbed|packed|left|put)\\b[^.?!]*\\b${wordPattern}\\b[^.?!]*\\b(on the table|in the bag|before getting on the bus)\\b`).test(text)
  ) {
    return true;
  }

  if (
    (category === "action" || category === "abstract") &&
    (new RegExp(`\\b(saw|noticed|enjoyed)\\b\\s+the\\s+${wordPattern}\\b`).test(text) ||
      new RegExp(`\\btalked about\\s+the\\s+${wordPattern}\\b`).test(text) ||
      new RegExp(`\\bthe\\s+${wordPattern}\\b\\s+on my way home\\b`).test(text))
  ) {
    return true;
  }

  return false;
}

function pickSemanticScene(clean: string, bookId: string, category: SemanticCategory, scenes: ExamplePair[]) {
  if (!scenes.length) return null;
  const history = readSceneTemplateHistory();
  const recentSet = new Set(history.slice(-24));
  const start = templateHash(`${bookId}:${clean}:${category}:semantic`) % scenes.length;
  for (let offset = 0; offset < scenes.length; offset += 1) {
    const idx = (start + offset) % scenes.length;
    const key = `semantic:${category}:${idx}`;
    if (!recentSet.has(key)) {
      rememberSceneTemplate(key, history);
      return scenes[idx];
    }
  }
  const fallback = scenes[start];
  rememberSceneTemplate(`semantic:${category}:${start}`, history);
  return fallback;
}

function semanticExampleFor(word: string, book: EnglishWordBook, meaning: string, pos: SceneTemplatePos): ExamplePair | null {
  if (pos === "adjective") return null;
  const clean = cleanToken(word) || word.trim().toLowerCase();
  const meaningHead = normalizeMeaningHead(meaning, clean);
  let category = inferSemanticCategory(clean, meaningHead, book.id);
  if (pos === "verb" && category === "generic") category = "action";

  const specificScenes: Record<string, ExamplePair[]> = {
    walk: [
      { example: "I walk to the office when the weather is good.", example_cn: "天气好的时候，我走路去办公室。" },
      { example: "After dinner, we walk slowly through the park.", example_cn: "晚饭后，我们在公园里慢慢散步。" },
      { example: "She decided to walk home instead of taking a taxi.", example_cn: "她决定走路回家，而不是打车。" },
      { example: "The doctor told him to walk more every day.", example_cn: "医生告诉他每天多走路。" },
    ],
    airport: [
      { example: "We arrived at the airport before sunrise.", example_cn: "\u6211\u4eec\u5728\u65e5\u51fa\u524d\u5230\u8fbe\u4e86\u673a\u573a\u3002" },
      { example: "The airport security line moved faster than expected.", example_cn: "\u673a\u573a\u5b89\u68c0\u961f\u4f0d\u6bd4\u9884\u60f3\u4e2d\u524d\u8fdb\u5f97\u66f4\u5feb\u3002" },
      { example: "She checked the gate number at the airport.", example_cn: "\u5979\u5728\u673a\u573a\u67e5\u770b\u4e86\u767b\u673a\u53e3\u53f7\u7801\u3002" },
      { example: "After the delay, families slept beside their bags at the airport.", example_cn: "\u822a\u73ed\u5ef6\u8bef\u540e\uff0c\u4e00\u4e9b\u5bb6\u5ead\u5728\u673a\u573a\u91cc\u9760\u7740\u884c\u674e\u7761\u7740\u4e86\u3002" },
      { example: "Because the airport was busy, we printed our boarding passes at home.", example_cn: "\u56e0\u4e3a\u673a\u573a\u5f88\u5fd9\uff0c\u6211\u4eec\u5728\u5bb6\u5148\u6253\u5370\u4e86\u767b\u673a\u724c\u3002" },
    ],
    bus: [
      { example: "The bus arrived just as it started to rain.", example_cn: "\u521a\u5f00\u59cb\u4e0b\u96e8\u65f6\uff0c\u516c\u4ea4\u8f66\u6b63\u597d\u5230\u4e86\u3002" },
      { example: "I tapped my card when I got on the bus.", example_cn: "\u6211\u4e0a\u516c\u4ea4\u8f66\u65f6\u5237\u4e86\u5361\u3002" },
      { example: "The last bus left ten minutes ago.", example_cn: "\u6700\u540e\u4e00\u73ed\u516c\u4ea4\u8f66\u5341\u5206\u949f\u524d\u5f00\u8d70\u4e86\u3002" },
      { example: "A student gave up his seat when the bus became crowded.", example_cn: "\u516c\u4ea4\u8f66\u53d8\u5f97\u62e5\u6324\u65f6\uff0c\u4e00\u540d\u5b66\u751f\u8ba9\u51fa\u4e86\u5ea7\u4f4d\u3002" },
      { example: "If the bus is late again, I will walk to the office.", example_cn: "\u5982\u679c\u516c\u4ea4\u8f66\u53c8\u665a\u70b9\uff0c\u6211\u5c31\u8d70\u8def\u53bb\u529e\u516c\u5ba4\u3002" },
    ],
    hotel: [
      { example: "We stayed at the hotel during the trip.", example_cn: "\u6211\u4eec\u65c5\u884c\u65f6\u4f4f\u5728\u8fd9\u5bb6\u65c5\u9986\u3002" },
      { example: "The hotel receptionist gave us two room keys.", example_cn: "\u9152\u5e97\u524d\u53f0\u7ed9\u4e86\u6211\u4eec\u4e24\u5f20\u623f\u5361\u3002" },
      { example: "Their hotel room overlooked the river.", example_cn: "\u4ed6\u4eec\u7684\u9152\u5e97\u623f\u95f4\u53ef\u4ee5\u4fef\u77b0\u6cb3\u9762\u3002" },
      { example: "The hotel lobby smelled of coffee and fresh flowers.", example_cn: "\u9152\u5e97\u5927\u5802\u91cc\u6709\u5496\u5561\u548c\u9c9c\u82b1\u7684\u6c14\u5473\u3002" },
      { example: "Although the hotel was small, the staff remembered every guest's name.", example_cn: "\u867d\u7136\u8fd9\u5bb6\u9152\u5e97\u4e0d\u5927\uff0c\u5458\u5de5\u5374\u8bb0\u5f97\u6bcf\u4f4d\u5ba2\u4eba\u7684\u540d\u5b57\u3002" },
    ],
    station: [
      { example: "The station platform was crowded during rush hour.", example_cn: "\u9ad8\u5cf0\u671f\u8f66\u7ad9\u7ad9\u53f0\u5f88\u62e5\u6324\u3002" },
      { example: "An announcement at the station changed our train time.", example_cn: "\u8f66\u7ad9\u7684\u4e00\u6761\u5e7f\u64ad\u6539\u4e86\u6211\u4eec\u7684\u5217\u8f66\u65f6\u95f4\u3002" },
      { example: "She waited by the station entrance with her suitcase.", example_cn: "\u5979\u62d6\u7740\u884c\u674e\u7bb1\u5728\u8f66\u7ad9\u5165\u53e3\u7b49\u5019\u3002" },
      { example: "The station clock was five minutes ahead of my phone.", example_cn: "\u8f66\u7ad9\u65f6\u949f\u6bd4\u6211\u7684\u624b\u673a\u5feb\u4e86\u4e94\u5206\u949f\u3002" },
      { example: "When the storm ended, people returned to the station quietly.", example_cn: "\u66b4\u98ce\u96e8\u7ed3\u675f\u540e\uff0c\u4eba\u4eec\u5b89\u9759\u5730\u56de\u5230\u8f66\u7ad9\u3002" },
    ],
  };
  const scenes = specificScenes[clean];
  if (scenes?.length) {
    return pickSemanticScene(clean, book.id, category, scenes);
  }

  const categoryScenes: Partial<Record<SemanticCategory, ExamplePair[]>> = {
    action: [
      { example: `I usually ${clean} for ten minutes after dinner.`, example_cn: `我通常晚饭后${meaningHead}十分钟。` },
      { example: `She likes to ${clean} when the weather is calm.`, example_cn: `天气平静的时候，她喜欢${meaningHead}。` },
      { example: `We decided to ${clean} before the day became too busy.`, example_cn: `我们决定在今天变得太忙之前先${meaningHead}。` },
      { example: `He stopped for a moment, then continued to ${clean}.`, example_cn: `他停了一会儿，然后继续${meaningHead}。` },
      { example: `On quiet weekends, they often ${clean} together.`, example_cn: `安静的周末，他们经常一起${meaningHead}。` },
    ],
    transport: [
      { example: `The ${clean} arrived earlier than the timetable showed.`, example_cn: `${meaningHead}\u6bd4\u65f6\u523b\u8868\u4e0a\u663e\u793a\u7684\u65f6\u95f4\u66f4\u65e9\u5230\u4e86\u3002` },
      { example: `She checked the ${clean} schedule before leaving home.`, example_cn: `\u5979\u51fa\u95e8\u524d\u67e5\u4e86${meaningHead}\u7684\u65f6\u523b\u8868\u3002` },
      { example: `Heavy rain delayed the ${clean} for several minutes.`, example_cn: `\u5927\u96e8\u8ba9${meaningHead}\u5ef6\u8bef\u4e86\u51e0\u5206\u949f\u3002` },
      { example: `I changed my route because the ${clean} was too crowded.`, example_cn: `\u56e0\u4e3a${meaningHead}\u592a\u62e5\u6324\uff0c\u6211\u6539\u4e86\u8def\u7ebf\u3002` },
      { example: `By the time we reached the ${clean}, the queue had already doubled.`, example_cn: `\u6211\u4eec\u5230\u8fbe${meaningHead}\u65f6\uff0c\u961f\u4f0d\u5df2\u7ecf\u53d8\u6210\u4e86\u4e24\u500d\u957f\u3002` },
    ],
    place: [
      { example: `The ${clean} opened early on Monday morning.`, example_cn: `${meaningHead}\u5468\u4e00\u65e9\u4e0a\u5f88\u65e9\u5c31\u5f00\u95e8\u4e86\u3002` },
      { example: `We stopped by the ${clean} on our way home.`, example_cn: `\u6211\u4eec\u56de\u5bb6\u8def\u4e0a\u987a\u8def\u53bb\u4e86${meaningHead}\u3002` },
      { example: `The ${clean} was quiet before the evening crowd arrived.`, example_cn: `\u665a\u9ad8\u5cf0\u4eba\u7fa4\u5230\u6765\u524d\uff0c${meaningHead}\u5f88\u5b89\u9759\u3002` },
      { example: `A handwritten sign on the ${clean} door explained the new hours.`, example_cn: `${meaningHead}\u95e8\u4e0a\u4e00\u5f20\u624b\u5199\u544a\u793a\u8bf4\u660e\u4e86\u65b0\u8425\u4e1a\u65f6\u95f4\u3002` },
      { example: `Although the ${clean} looked small from outside, it was bright and busy inside.`, example_cn: `\u867d\u7136${meaningHead}\u4ece\u5916\u9762\u770b\u4e0d\u5927\uff0c\u91cc\u9762\u5374\u660e\u4eae\u53c8\u5fd9\u788c\u3002` },
    ],
    food: [
      { example: `She bought ${clean} from a small shop nearby.`, example_cn: `\u5979\u5728\u9644\u8fd1\u7684\u5c0f\u5e97\u4e70\u4e86${meaningHead}\u3002` },
      { example: `The fresh ${clean} smelled warm from the oven.`, example_cn: `\u65b0\u9c9c\u7684${meaningHead}\u5e26\u7740\u521a\u51fa\u7089\u7684\u70ed\u9999\u3002` },
      { example: `He saved some ${clean} for tomorrow's breakfast.`, example_cn: `\u4ed6\u7559\u4e86\u4e00\u4e9b${meaningHead}\u7ed9\u660e\u5929\u65e9\u9910\u3002` },
      { example: `The children shared the ${clean} before the picnic began.`, example_cn: `\u91ce\u9910\u5f00\u59cb\u524d\uff0c\u5b69\u5b50\u4eec\u5206\u4eab\u4e86${meaningHead}\u3002` },
      { example: `Because the ${clean} was still hot, she wrapped it in a napkin.`, example_cn: `\u56e0\u4e3a${meaningHead}\u8fd8\u5f88\u70ed\uff0c\u5979\u7528\u9910\u5dfe\u628a\u5b83\u5305\u4e86\u8d77\u6765\u3002` },
    ],
    portable_object: [
      { example: `I put the ${clean} beside my laptop.`, example_cn: `\u6211\u628a${meaningHead}\u653e\u5728\u7b14\u8bb0\u672c\u7535\u8111\u65c1\u8fb9\u3002` },
      { example: `She found the ${clean} at the bottom of her bag.`, example_cn: `\u5979\u5728\u5305\u5e95\u627e\u5230\u4e86${meaningHead}\u3002` },
      { example: `Please bring the ${clean} to the front desk.`, example_cn: `\u8bf7\u628a${meaningHead}\u5e26\u5230\u524d\u53f0\u3002` },
    ],
    work_item: [
      { example: `The team reviewed the ${clean} before the meeting.`, example_cn: `\u56e2\u961f\u5728\u4f1a\u524d\u590d\u76d8\u4e86${meaningHead}\u3002` },
      { example: `She updated the ${clean} after the client call.`, example_cn: `\u5979\u5728\u5ba2\u6237\u7535\u8bdd\u540e\u66f4\u65b0\u4e86${meaningHead}\u3002` },
      { example: `The manager highlighted the ${clean} in the weekly report.`, example_cn: `\u7ecf\u7406\u5728\u5468\u62a5\u4e2d\u5f3a\u8c03\u4e86${meaningHead}\u3002` },
      { example: `Before anyone made a decision, the ${clean} was pinned to the screen.`, example_cn: `\u5728\u4efb\u4f55\u4eba\u505a\u51b3\u5b9a\u4e4b\u524d\uff0c${meaningHead}\u88ab\u56fa\u5b9a\u5728\u5c4f\u5e55\u4e0a\u3002` },
      { example: `Although the ${clean} looked simple, it changed the team's priorities.`, example_cn: `\u867d\u7136${meaningHead}\u770b\u8d77\u6765\u7b80\u5355\uff0c\u5b83\u5374\u6539\u53d8\u4e86\u56e2\u961f\u7684\u4f18\u5148\u7ea7\u3002` },
    ],
    tech: [
      { example: `The engineer checked the ${clean} before deployment.`, example_cn: `\u5de5\u7a0b\u5e08\u5728\u90e8\u7f72\u524d\u68c0\u67e5\u4e86${meaningHead}\u3002` },
      { example: `The logs showed a problem with the ${clean}.`, example_cn: `\u65e5\u5fd7\u663e\u793a${meaningHead}\u51fa\u73b0\u4e86\u95ee\u9898\u3002` },
      { example: `They optimized the ${clean} before the release.`, example_cn: `\u4ed6\u4eec\u5728\u53d1\u5e03\u524d\u4f18\u5316\u4e86${meaningHead}\u3002` },
      { example: `Once the ${clean} warmed up, the dashboard loaded almost instantly.`, example_cn: `${meaningHead}\u9884\u70ed\u5b8c\u6210\u540e\uff0c\u4eea\u8868\u76d8\u51e0\u4e4e\u7acb\u523b\u52a0\u8f7d\u5b8c\u6210\u3002` },
      { example: `If the ${clean} fails again, the worker will switch to a backup path.`, example_cn: `\u5982\u679c${meaningHead}\u518d\u6b21\u5931\u8d25\uff0cworker \u4f1a\u5207\u5230\u5907\u7528\u8def\u5f84\u3002` },
    ],
    time: [
      { example: `The ${clean} was quiet and cool.`, example_cn: `${meaningHead}\u5f88\u5b89\u9759\uff0c\u4e5f\u5f88\u51c9\u723d\u3002` },
      { example: `I saved this task for the ${clean}.`, example_cn: `\u6211\u628a\u8fd9\u4e2a\u4efb\u52a1\u7559\u5230${meaningHead}\u5904\u7406\u3002` },
      { example: `The ${clean} felt shorter than usual.`, example_cn: `${meaningHead}\u611f\u89c9\u6bd4\u5e73\u65f6\u66f4\u77ed\u3002` },
    ],
    abstract: [
      { example: `The discussion focused on the ${clean}.`, example_cn: `\u8ba8\u8bba\u96c6\u4e2d\u5728${meaningHead}\u4e0a\u3002` },
      { example: `Her answer changed our view of the ${clean}.`, example_cn: `\u5979\u7684\u56de\u7b54\u6539\u53d8\u4e86\u6211\u4eec\u5bf9${meaningHead}\u7684\u770b\u6cd5\u3002` },
      { example: `The report explains the ${clean} with clear examples.`, example_cn: `\u62a5\u544a\u7528\u6e05\u6670\u4f8b\u5b50\u89e3\u91ca\u4e86${meaningHead}\u3002` },
    ],
  };
  const options = categoryScenes[category];
  if (options?.length) {
    return options[templateHash(`${book.id}:${clean}:${category}`) % options.length];
  }
  return null;
}

const SCENE_TEMPLATE_HISTORY_KEY = "jachin.english_vocab.scene_template_history.v2";
const SCENE_TEMPLATE_HISTORY_LIMIT = 80;

const dailyTemplates: Record<SceneTemplatePos, SceneTemplate[]> = {
  noun: [
    {
      id: "daily_noun_1",
      example: (word) => `The ${word} was easy to notice in the room.`,
      example_cn: (meaning) => `在房间里，${meaning}很容易被注意到。`,
    },
    {
      id: "daily_noun_2",
      example: (word) => `The ${word} became important later that day.`,
      example_cn: (meaning) => `${meaning}在那天晚些时候变得很重要。`,
    },
    {
      id: "daily_noun_3",
      example: (word) => `She asked a clear question about the ${word}.`,
      example_cn: (meaning) => `她问了一个关于${meaning}的清楚问题。`,
    },
    {
      id: "daily_noun_4",
      example: (word) => `She explained the ${word} with a simple example.`,
      example_cn: (meaning) => `她用一个简单的例子解释了${meaning}。`,
    },
  ],
  verb: [
    {
      id: "daily_verb_1",
      example: (word) => `I usually ${word} before breakfast.`,
      example_cn: (meaning) => `我通常在早餐前会${meaning}。`,
    },
    {
      id: "daily_verb_2",
      example: (word) => `Can you ${word} this while I answer the phone?`,
      example_cn: (meaning) => `我接电话时你能先${meaning}这件事吗？`,
    },
    {
      id: "daily_verb_3",
      example: (word) => `We had to ${word} quickly before leaving home.`,
      example_cn: (meaning) => `出门前我们得赶紧${meaning}。`,
    },
    {
      id: "daily_verb_4",
      example: (word) => `They ${word} together after dinner every day.`,
      example_cn: (meaning) => `他们每天晚饭后都会一起${meaning}。`,
    },
  ],
  adjective: [
    {
      id: "daily_adj_1",
      example: (word) => `This room feels ${word} after we open the window.`,
      example_cn: (meaning) => `开窗后这个房间感觉更${meaning}了。`,
    },
    {
      id: "daily_adj_2",
      example: (word) => `The new route is much more ${word} for commuting.`,
      example_cn: (meaning) => `这条新路线通勤时更${meaning}。`,
    },
    {
      id: "daily_adj_3",
      example: (word) => `Her idea sounds ${word} for a busy weekday.`,
      example_cn: (meaning) => `她这个想法在工作日里听起来很${meaning}。`,
    },
    {
      id: "daily_adj_4",
      example: (word) => `That choice was ${word} and saved us time.`,
      example_cn: (meaning) => `那个选择很${meaning}，还帮我们省了时间。`,
    },
  ],
  other: [
    {
      id: "daily_other_1",
      example: (word) => `We paused, and ${word} the room became quiet.`,
      example_cn: (meaning) => `我们停顿了一下，${meaning}房间里安静了下来。`,
    },
    {
      id: "daily_other_2",
      example: (word) => `I looked around and ${word} noticed the sign.`,
      example_cn: (meaning) => `我环顾四周，${meaning}注意到了那个标志。`,
    },
    {
      id: "daily_other_3",
      example: (word) => `She hesitated, ${word} gave her final answer.`,
      example_cn: (meaning) => `她犹豫了一下，${meaning}给出了最终答案。`,
    },
    {
      id: "daily_other_4",
      example: (word) => `He took a breath and ${word} continued speaking.`,
      example_cn: (meaning) => `他深吸一口气，${meaning}继续说下去。`,
    },
  ],
};

const workplaceTemplates: Record<SceneTemplatePos, SceneTemplate[]> = {
  noun: [
    {
      id: "work_noun_1",
      example: (word) => `The team reviewed ${word} before the client call.`,
      example_cn: (meaning) => `团队在客户电话前复盘了${meaning}。`,
    },
    {
      id: "work_noun_2",
      example: (word) => `Please add ${word} to today's meeting agenda.`,
      example_cn: (meaning) => `请把${meaning}加到今天会议议程里。`,
    },
    {
      id: "work_noun_3",
      example: (word) => `Finance approved the ${word} for next quarter.`,
      example_cn: (meaning) => `财务批准了下季度的${meaning}。`,
    },
    {
      id: "work_noun_4",
      example: (word) => `We tracked ${word} in the weekly report.`,
      example_cn: (meaning) => `我们在周报中跟踪了${meaning}。`,
    },
  ],
  verb: [
    {
      id: "work_verb_1",
      example: (word) => `We need to ${word} the proposal before noon.`,
      example_cn: (meaning) => `我们需要在中午前${meaning}这份提案。`,
    },
    {
      id: "work_verb_2",
      example: (word) => `Could you ${word} this draft and send feedback?`,
      example_cn: (meaning) => `你可以先${meaning}这份草稿再给反馈吗？`,
    },
    {
      id: "work_verb_3",
      example: (word) => `Let's ${word} the timeline after the stand-up.`,
      example_cn: (meaning) => `站会后我们来${meaning}时间线。`,
    },
    {
      id: "work_verb_4",
      example: (word) => `I will ${word} the numbers before sharing them.`,
      example_cn: (meaning) => `我会先${meaning}数据再发出去。`,
    },
  ],
  adjective: [
    {
      id: "work_adj_1",
      example: (word) => `We need a more ${word} process for onboarding.`,
      example_cn: (meaning) => `我们需要一个更${meaning}的新员工入职流程。`,
    },
    {
      id: "work_adj_2",
      example: (word) => `The report is ${word} enough for leadership review.`,
      example_cn: (meaning) => `这份报告已经足够${meaning}，可以交领导审阅。`,
    },
    {
      id: "work_adj_3",
      example: (word) => `This timeline looks ${word} for both teams.`,
      example_cn: (meaning) => `这个时间安排对两边团队都比较${meaning}。`,
    },
    {
      id: "work_adj_4",
      example: (word) => `Their response stayed ${word} and factual.`,
      example_cn: (meaning) => `他们的回复保持了${meaning}且基于事实。`,
    },
  ],
  other: [
    {
      id: "work_other_1",
      example: (word) => `In the update, ${word} we proposed a safer rollout.`,
      example_cn: (meaning) => `在更新里，${meaning}我们提出了更稳妥的发布方案。`,
    },
    {
      id: "work_other_2",
      example: (word) => `The manager agreed, ${word} the team moved forward.`,
      example_cn: (meaning) => `经理同意了，${meaning}团队继续推进。`,
    },
    {
      id: "work_other_3",
      example: (word) => `We adjusted the timeline, ${word} reduced delivery risk.`,
      example_cn: (meaning) => `我们调整了时间线，${meaning}降低了交付风险。`,
    },
    {
      id: "work_other_4",
      example: (word) => `The report was clear, ${word} leadership approved it quickly.`,
      example_cn: (meaning) => `报告很清晰，${meaning}管理层很快批准了它。`,
    },
  ],
};

const examTemplates: Record<SceneTemplatePos, SceneTemplate[]> = {
  noun: [
    {
      id: "exam_noun_1",
      example: (word) => `Contemporary research suggests that ${word} shapes social outcomes.`,
      example_cn: (meaning) => `当代研究表明，${meaning}会影响社会结果。`,
    },
    {
      id: "exam_noun_2",
      example: (word) => `A balanced essay should examine the limits of ${word}.`,
      example_cn: (meaning) => `一篇平衡的文章应当讨论${meaning}的局限。`,
    },
    {
      id: "exam_noun_3",
      example: (word) => `Public debates increasingly focus on the impact of ${word}.`,
      example_cn: (meaning) => `公共讨论越来越关注${meaning}的影响。`,
    },
    {
      id: "exam_noun_4",
      example: (word) => `Historical evidence shows that ${word} changes over time.`,
      example_cn: (meaning) => `历史证据显示，${meaning}会随时间变化。`,
    },
  ],
  verb: [
    {
      id: "exam_verb_1",
      example: (word) => `Governments should ${word} policy goals with long-term equity in mind.`,
      example_cn: (meaning) => `政府在制定政策目标时应当兼顾长期公平地${meaning}。`,
    },
    {
      id: "exam_verb_2",
      example: (word) => `Universities often ${word} evidence from multiple disciplines.`,
      example_cn: (meaning) => `大学通常会从多学科证据中${meaning}。`,
    },
    {
      id: "exam_verb_3",
      example: (word) => `Scholars ${word} assumptions before drawing conclusions.`,
      example_cn: (meaning) => `学者在下结论前会先${meaning}各种假设。`,
    },
    {
      id: "exam_verb_4",
      example: (word) => `A strong argument must ${word} both causes and consequences.`,
      example_cn: (meaning) => `有力的论证必须同时${meaning}原因与后果。`,
    },
  ],
  adjective: [
    {
      id: "exam_adj_1",
      example: (word) => `The policy appears ${word}, yet implementation remains uneven.`,
      example_cn: (meaning) => `该政策看似${meaning}，但执行仍不均衡。`,
    },
    {
      id: "exam_adj_2",
      example: (word) => `This trend is increasingly ${word} across major cities.`,
      example_cn: (meaning) => `这一趋势在主要城市中正变得越来越${meaning}。`,
    },
    {
      id: "exam_adj_3",
      example: (word) => `The data is ${word} enough to challenge earlier assumptions.`,
      example_cn: (meaning) => `这组数据已足够${meaning}，能够挑战先前假设。`,
    },
    {
      id: "exam_adj_4",
      example: (word) => `Such reforms are politically ${word} but socially contested.`,
      example_cn: (meaning) => `这类改革在政治上${meaning}，但在社会层面仍有争议。`,
    },
  ],
  other: [
    {
      id: "exam_other_1",
      example: (word) => `In academic writing, ${word} can mark a clear contrast.`,
      example_cn: (meaning) => `在学术写作中，${meaning}可以明确标记对比关系。`,
    },
    {
      id: "exam_other_2",
      example: (word) => `The paragraph reads better when ${word} links two claims.`,
      example_cn: (meaning) => `当${meaning}连接两个论点时，这段文字读起来更顺畅。`,
    },
    {
      id: "exam_other_3",
      example: (word) => `A high-band response uses ${word} with control and precision.`,
      example_cn: (meaning) => `高分回答会更克制且精准地使用${meaning}。`,
    },
    {
      id: "exam_other_4",
      example: (word) => `The argument stays coherent because ${word} guides the transition.`,
      example_cn: (meaning) => `论证保持连贯，是因为${meaning}引导了过渡关系。`,
    },
  ],
};

const computerScienceTemplates: Record<SceneTemplatePos, SceneTemplate[]> = {
  noun: [
    {
      id: "cs_noun_1",
      example: (word) => `The engineer inspected ${word} before deployment.`,
      example_cn: (meaning) => `工程师在部署前检查了${meaning}。`,
    },
    {
      id: "cs_noun_2",
      example: (word) => `Our logs captured ${word} during the outage.`,
      example_cn: (meaning) => `系统日志在故障期间记录了${meaning}。`,
    },
    {
      id: "cs_noun_3",
      example: (word) => `The patch improved ${word} across all services.`,
      example_cn: (meaning) => `这个补丁提升了所有服务的${meaning}。`,
    },
    {
      id: "cs_noun_4",
      example: (word) => `We tracked ${word} in yesterday's benchmark report.`,
      example_cn: (meaning) => `我们在昨天的基准报告里跟踪了${meaning}。`,
    },
  ],
  verb: [
    {
      id: "cs_verb_1",
      example: (word) => `We need to ${word} the service before release.`,
      example_cn: (meaning) => `发布前我们需要先${meaning}这个服务。`,
    },
    {
      id: "cs_verb_2",
      example: (word) => `The script can ${word} each file automatically.`,
      example_cn: (meaning) => `这个脚本可以自动${meaning}每个文件。`,
    },
    {
      id: "cs_verb_3",
      example: (word) => `They ${word} requests in batches to reduce latency.`,
      example_cn: (meaning) => `他们按批次${meaning}请求来降低延迟。`,
    },
    {
      id: "cs_verb_4",
      example: (word) => `Please ${word} the config and restart the worker.`,
      example_cn: (meaning) => `请先${meaning}配置后重启worker。`,
    },
  ],
  adjective: [
    {
      id: "cs_adj_1",
      example: (word) => `The new implementation is ${word} and easier to maintain.`,
      example_cn: (meaning) => `新实现更${meaning}，也更容易维护。`,
    },
    {
      id: "cs_adj_2",
      example: (word) => `This endpoint stays ${word} under heavy traffic.`,
      example_cn: (meaning) => `这个接口在高流量下依然保持${meaning}。`,
    },
    {
      id: "cs_adj_3",
      example: (word) => `The rollout plan looks ${word} for production.`,
      example_cn: (meaning) => `这个发布计划用于生产环境看起来很${meaning}。`,
    },
    {
      id: "cs_adj_4",
      example: (word) => `Their fix is ${word}, but we still need regression tests.`,
      example_cn: (meaning) => `他们的修复很${meaning}，但我们仍需回归测试。`,
    },
  ],
  other: [
    {
      id: "cs_other_1",
      example: (word) => `In the design doc, ${word} clarifies how modules interact.`,
      example_cn: (meaning) => `在设计文档中，${meaning}阐明了模块如何交互。`,
    },
    {
      id: "cs_other_2",
      example: (word) => `The runbook became clearer once ${word} connected two steps.`,
      example_cn: (meaning) => `当${meaning}连接了两个步骤后，运行手册更清晰了。`,
    },
    {
      id: "cs_other_3",
      example: (word) => `During incident review, ${word} explained the failure chain.`,
      example_cn: (meaning) => `在故障复盘中，${meaning}解释了故障传播链路。`,
    },
    {
      id: "cs_other_4",
      example: (word) => `In the postmortem, ${word} made the root cause easier to follow.`,
      example_cn: (meaning) => `在复盘中，${meaning}让根因更容易被理解。`,
    },
  ],
};

const cefrA2Templates: Record<SceneTemplatePos, SceneTemplate[]> = {
  noun: [
    {
      id: "a2_n_1",
      example: (word) => `There is a ${word} near my home.`,
      example_cn: (meaning) => `我家附近有一个${meaning}。`,
    },
    {
      id: "a2_n_2",
      example: (word) => `We went to the ${word} after class.`,
      example_cn: (meaning) => `我们下课后去了${meaning}。`,
    },
  ],
  verb: [
    {
      id: "a2_v_1",
      example: (word) => `I ${word} the list before I leave.`,
      example_cn: (meaning) => `我离开前会先${meaning}这个清单。`,
    },
    {
      id: "a2_v_2",
      example: (word) => `We ${word} together every evening.`,
      example_cn: (meaning) => `我们每天傍晚会一起${meaning}。`,
    },
  ],
  adjective: [
    {
      id: "a2_a_1",
      example: (word) => `This idea is very ${word}.`,
      example_cn: (meaning) => `这个想法非常${meaning}。`,
    },
    {
      id: "a2_a_2",
      example: (word) => `The new plan feels ${word} now.`,
      example_cn: (meaning) => `这个新计划现在看起来很${meaning}。`,
    },
  ],
  other: [
    {
      id: "a2_o_1",
      example: (word) => `He stopped, and ${word} smiled at me.`,
      example_cn: (meaning) => `他停下来，${meaning}朝我笑了笑。`,
    },
    {
      id: "a2_o_2",
      example: (word) => `I turned around and ${word} saw my friend.`,
      example_cn: (meaning) => `我转过身，${meaning}看见了我的朋友。`,
    },
  ],
};

const cefrB1Templates: Record<SceneTemplatePos, SceneTemplate[]> = {
  noun: [
    {
      id: "b1_n_1",
      example: (word) => `In our discussion, the ${word} came up several times.`,
      example_cn: (meaning) => `在讨论中，这个${meaning}被多次提到。`,
    },
    {
      id: "b1_n_2",
      example: (word) => `We chose the ${word} because it fit our needs.`,
      example_cn: (meaning) => `我们选择这个${meaning}，因为它更符合需求。`,
    },
  ],
  verb: [
    {
      id: "b1_v_1",
      example: (word) => `I usually ${word} this part before asking for help.`,
      example_cn: (meaning) => `我通常会先${meaning}这部分，再去求助。`,
    },
    {
      id: "b1_v_2",
      example: (word) => `They ${word} the issue quickly when time is limited.`,
      example_cn: (meaning) => `在时间有限时，他们会快速${meaning}这个问题。`,
    },
  ],
  adjective: [
    {
      id: "b1_a_1",
      example: (word) => `This method is more ${word} than our old one.`,
      example_cn: (meaning) => `这个方法比我们旧的方法更${meaning}。`,
    },
    {
      id: "b1_a_2",
      example: (word) => `The new schedule looks ${word} for everyone.`,
      example_cn: (meaning) => `新日程安排对大家来说看起来更${meaning}。`,
    },
  ],
  other: [
    {
      id: "b1_o_1",
      example: (word) => `The meeting slowed down, ${word} everyone became more focused.`,
      example_cn: (meaning) => `会议节奏放缓了，${meaning}大家更专注了。`,
    },
    {
      id: "b1_o_2",
      example: (word) => `We revised the draft, ${word} the argument became clearer.`,
      example_cn: (meaning) => `我们修改了草稿，${meaning}论点更清晰了。`,
    },
  ],
};

const cefrB2Templates: Record<SceneTemplatePos, SceneTemplate[]> = {
  noun: [
    {
      id: "b2_n_1",
      example: (word) => `Although views differ, the ${word} remains central to the debate.`,
      example_cn: (meaning) => `尽管观点不同，这个${meaning}仍是讨论核心。`,
    },
    {
      id: "b2_n_2",
      example: (word) => `The report links the ${word} to wider social change.`,
      example_cn: (meaning) => `报告将这个${meaning}与更广泛的社会变化联系起来。`,
    },
  ],
  verb: [
    {
      id: "b2_v_1",
      example: (word) => `Policy makers must ${word} competing priorities with clear evidence.`,
      example_cn: (meaning) => `政策制定者必须基于清晰证据去${meaning}相互竞争的优先事项。`,
    },
    {
      id: "b2_v_2",
      example: (word) => `Researchers ${word} multiple variables before making claims.`,
      example_cn: (meaning) => `研究者在提出结论前会${meaning}多个变量。`,
    },
  ],
  adjective: [
    {
      id: "b2_a_1",
      example: (word) => `The proposal appears ${word}, yet it still requires rigorous testing.`,
      example_cn: (meaning) => `该提案看似${meaning}，但仍需要严格验证。`,
    },
    {
      id: "b2_a_2",
      example: (word) => `Such an approach is politically ${word} but socially sensitive.`,
      example_cn: (meaning) => `这种方法在政治上${meaning}，但在社会层面较敏感。`,
    },
  ],
  other: [
    {
      id: "b2_o_1",
      example: (word) => `In formal writing, ${word} can strengthen logical cohesion.`,
      example_cn: (meaning) => `在正式写作中，${meaning}可以增强逻辑连贯性。`,
    },
    {
      id: "b2_o_2",
      example: (word) => `A high-level response uses ${word} with precision and restraint.`,
      example_cn: (meaning) => `高水平回答会精准且克制地使用${meaning}。`,
    },
  ],
};

function readSceneTemplateHistory(): string[] {
  try {
    const raw = localStorage.getItem(SCENE_TEMPLATE_HISTORY_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.map((x) => String(x)) : [];
  } catch {
    return [];
  }
}

function writeSceneTemplateHistory(history: string[]) {
  try {
    localStorage.setItem(SCENE_TEMPLATE_HISTORY_KEY, JSON.stringify(history.slice(-SCENE_TEMPLATE_HISTORY_LIMIT)));
  } catch {
    // Template history only improves diversity; failures should not block lookup.
  }
}

function templateHash(text: string): number {
  let hash = 0;
  for (let i = 0; i < text.length; i += 1) {
    hash = (hash * 31 + text.charCodeAt(i)) >>> 0;
  }
  return hash;
}

function rememberSceneTemplate(templateKey: string, history: string[]) {
  const next = history.filter((item) => item !== templateKey);
  next.push(templateKey);
  writeSceneTemplateHistory(next);
}

function normalizeSceneTemplatePos(rawPos: string): SceneTemplatePos {
  const lower = rawPos.toLowerCase();
  if (/\bn\./.test(lower) || lower.includes("noun")) return "noun";
  if (/\bv\./.test(lower) || lower.includes("verb")) return "verb";
  if (/\badj\./.test(lower) || lower.includes("adjective")) return "adjective";
  return "noun";
}

function sceneTemplatePool(bookId: string, pos: SceneTemplatePos): SceneTemplate[] {
  if (bookId === "workplace") return workplaceTemplates[pos];
  if (bookId === "computer_science") return computerScienceTemplates[pos];
  if (bookId === "ielts_academic" || bookId === "toefl_academic") return examTemplates[pos];
  return dailyTemplates[pos];
}

function pickSceneTemplate(word: string, bookId: string, templates: SceneTemplate[]): SceneTemplate {
  if (!templates.length) {
    return {
      id: "default_template",
      example: (w) => `I noticed ${w} in today's sentence.`,
      example_cn: (meaning) => `我在今天的例句里注意到了${meaning}。`,
    };
  }
  const history = readSceneTemplateHistory();
  const recentSet = new Set(history.slice(-SCENE_TEMPLATE_HISTORY_LIMIT));
  const start = templateHash(`${bookId}:${word}`) % templates.length;
  for (let offset = 0; offset < templates.length; offset += 1) {
    const idx = (start + offset) % templates.length;
    const candidate = templates[idx];
    const key = `${bookId}:${candidate.id}`;
    if (!recentSet.has(key)) {
      rememberSceneTemplate(key, history);
      return candidate;
    }
  }
  const fallback = templates[start];
  rememberSceneTemplate(`${bookId}:${fallback.id}`, history);
  return fallback;
}

function pickTemplateBySeed(templates: SceneTemplate[], seed: string): SceneTemplate {
  if (!templates.length) {
    return {
      id: "default_explicit",
      example: (word) => `I noticed ${word} in this sentence.`,
      example_cn: (meaning) => `我在这个句子里注意到了${meaning}。`,
    };
  }
  const start = templateHash(seed) % templates.length;
  return templates[start];
}

function normalizeMeaningHead(meaning: string, word: string) {
  const raw = (meaning || "")
    .split(/[；;。]/)[0]
    .replace(/\s+/g, "")
    .trim();
  const stripped = raw.replace(new RegExp(`^${word}[:：]`, "i"), "").trim();
  return stripped || word;
}

function buildExplicitExampleVariants(result: LookupResult | null, book: EnglishWordBook): ExampleVariant[] {
  if (!result) return [];
  const word = cleanToken(result.word) || result.word.trim().toLowerCase();
  if (!word) return [];
  const pos = normalizeSceneTemplatePos(result.part_of_speech || getWordMetadata(book, word)?.partOfSpeech || "");
  const meaning = normalizeMeaningHead(result.meaning_cn || definitionFor(word)?.meaning_cn || word, word);
  const categories: Array<{ id: string; label: string; pool: SceneTemplate[] }> = [
    { id: "daily", label: "日常口语", pool: dailyTemplates[pos] },
    { id: "exam", label: "考试写作（高级）", pool: examTemplates[pos] },
    { id: "work", label: "职场商务", pool: workplaceTemplates[pos] },
    { id: "a2", label: "A2 基础", pool: cefrA2Templates[pos] },
    { id: "b1", label: "B1 进阶", pool: cefrB1Templates[pos] },
    { id: "b2", label: "B2 高阶", pool: cefrB2Templates[pos] },
  ];
  const used = new Set<string>();
  return categories.map((category) => {
    const seed = `${word}:${category.id}:${pos}`;
    const selected = pickTemplateBySeed(category.pool, seed);
    let chosen = selected;
    const start = category.pool.length ? templateHash(seed) % category.pool.length : 0;
    for (let offset = 0; offset < category.pool.length; offset += 1) {
      const candidate = category.pool[(start + offset) % category.pool.length];
      const candidateText = candidate.example(word);
      if (!used.has(candidateText)) {
        chosen = candidate;
        break;
      }
    }
    const example = chosen.example(word);
    used.add(example);
    return {
      id: category.id,
      label: category.label,
      example,
      example_cn: chosen.example_cn(meaning),
    };
  });
}

function shouldUseExplicitVariants(result: LookupResult) {
  const source = (result.source || "").toLowerCase();
  const model = (result.model || "").toLowerCase();
  return source.includes("local_scene") || source.includes("local_fallback") || model.includes("local_scene");
}

function pickRandomVariantExcluding(variants: ExampleVariant[], excludedId?: string | null): ExampleVariant | null {
  if (!variants.length) return null;
  const start = Math.floor(Math.random() * variants.length);
  for (let offset = 0; offset < variants.length; offset += 1) {
    const candidate = variants[(start + offset) % variants.length];
    if (!excludedId || candidate.id !== excludedId) return candidate;
  }
  return variants[start];
}

function makeExample(word: string, book: EnglishWordBook): ExamplePair {
  const cleanWord = cleanToken(word) || word.trim().toLowerCase();
  const curated = curatedExamples[cleanWord];
  if (curated) return curated;
  const meaningHead =
    definitionFor(cleanWord)
      ?.meaning_cn.split(/[；;，,]/)[0]
      ?.replace(/\s+/g, "")
      ?.trim() || cleanWord;
  const pos =
    getWordMetadata(book, cleanWord)?.partOfSpeech ??
    definitionFor(cleanWord)?.part_of_speech ??
    "";
  const normalizedPos = normalizeSceneTemplatePos(pos);
  const semanticPair = semanticExampleFor(cleanWord, book, meaningHead, normalizedPos);
  if (semanticPair && !isSemanticallyBadExample(semanticPair.example, cleanWord, meaningHead)) {
    return semanticPair;
  }
  const pool = sceneTemplatePool(book.id, normalizedPos);
  const template = pickSceneTemplate(cleanWord, book.id, pool);
  return {
    example: template.example(cleanWord),
    example_cn: template.example_cn(meaningHead),
  };
}

function normalizeLookupResult(result: LookupResult, book: EnglishWordBook): LookupResult {
  if (hasDisplayableExample(result)) return result;
  if (
    result.refresh_hint === "background_ai_refresh" ||
    result.source === "final_example_not_ready" ||
    result.source === "local_fallback" ||
    result.source === "local_context_fallback"
  ) {
    return {
      ...result,
      example: "",
      example_cn: "",
    };
  }
  const word = cleanToken(result.word) || result.word.trim().toLowerCase();
  const fallbackPair = word ? makeExample(word, book) : null;
  if (
    fallbackPair?.example &&
    !isPlaceholderExample(fallbackPair.example) &&
    !isSemanticallyBadExample(fallbackPair.example, word, result.meaning_cn)
  ) {
    const completed = {
      ...result,
      example: fallbackPair.example,
      example_cn: fallbackPair.example_cn,
      source: "local_fast_example",
      model: "local_fast_template",
    };
    if (hasDisplayableExample(completed)) return completed;
  }
  return {
    ...result,
    example: "",
    example_cn: "",
  };
}

function isWeakLookupResult(result?: LookupResult | null) {
  if (!result) return true;
  const meaning = (result.meaning_cn || "").trim();
  const contextLookup = isContextLookupSource(result);
  const trustedModel = isTrustedModelSource(result);
  return (
    result.source === "local_fallback" ||
    result.source === "local_context_fallback" ||
    isFallbackExampleSource(result) ||
    meaning.includes(UI.fallbackMeaning) ||
    meaning.includes("\u6a21\u578b\u6b63\u5728\u8865\u5168") ||
    meaning.includes("\u8865\u5168\u8be5\u8bcd\u91ca\u4e49") ||
    meaning.includes("\u53ef\u5148\u6309\u4f8b\u53e5\u8bed\u5883\u8bb0\u5fc6") ||
    meaning.includes("\u540e\u53f0\u4f1a\u81ea\u52a8\u8865\u5168") ||
    (!contextLookup &&
      (isPlaceholderExample(result.example) ||
        (!trustedModel && isSemanticallyBadExample(result.example, result.word, result.meaning_cn)) ||
        !result.example_cn?.trim()))
  );
}

function isForegroundQualityLookup(result?: LookupResult | null): boolean {
  if (!result || !hasDisplayableExample(result) || isWeakLookupResult(result)) return false;
  return isTrustedModelSource(result);
}

function canUseWithoutRemote(result?: LookupResult | null): boolean {
  if (isForegroundQualityLookup(result)) return true;
  return Boolean(result && result.source === "local_ecdict" && hasDisplayableExample(result) && !isWeakLookupResult(result));
}

function preferLookupResult(incoming: LookupResult, existing: LookupResult | null | undefined, book: EnglishWordBook) {
  const normalizedIncoming = normalizeLookupResult(incoming, book);
  const normalizedExisting = existing ? normalizeLookupResult(existing, book) : null;
  if (!normalizedExisting) return normalizedIncoming;
  if (isWeakLookupResult(normalizedIncoming) && !isWeakLookupResult(normalizedExisting)) {
    return normalizedExisting;
  }
  if (!isWeakLookupResult(normalizedIncoming) && isWeakLookupResult(normalizedExisting)) {
    return normalizedIncoming;
  }
  if (
    normalizedExisting.source === "local_ecdict" &&
    !isWeakLookupResult(normalizedExisting) &&
    normalizedIncoming.source !== "local_ecdict"
  ) {
    return normalizedExisting;
  }
  return normalizedIncoming;
}

function localLookupResult(word: string, book: EnglishWordBook): LookupResult | null {
  const key = cleanToken(word);
  if (!key) return null;
  const matched = lookupCandidateEntries(key).find((candidate) => definitionFor(candidate.word));
  if (!matched) return null;
  const definition = definitionFor(matched.word);
  if (!definition) return null;
  const metadata = getWordMetadata(book, matched.word);
  const meaning =
    matched.word === key
      ? definition.meaning_cn
      : `${key} \u662f ${matched.word} \u7684${matched.relation ?? "\u53d8\u5f62"}\uff1b${definition.meaning_cn}`;
  const pair = curatedExampleFor(matched.word) ?? curatedExampleFor(key);
  return {
    word: key,
    phonetic: metadata?.phonetic ?? "-",
    part_of_speech: metadata?.partOfSpeech ?? definition.part_of_speech ?? "-",
    meaning_cn: meaning,
    example: pair?.example ?? "",
    example_cn: pair?.example_cn ?? "",
    source: pair ? "local_ecdict" : "local_ecdict_definition",
    model: "local",
  };
}

function fallbackLookupResult(word: string, book: EnglishWordBook): LookupResult {
  const key = cleanToken(word) || word.trim().toLowerCase();
  const metadata = getWordMetadata(book, key);
  return {
    word: key || word,
    phonetic: metadata?.phonetic ?? "-",
    part_of_speech: metadata?.partOfSpeech ?? "-",
    meaning_cn: `${key || word}\uff1a${UI.fallbackMeaning}`,
    example: "",
    example_cn: "",
    source: "local_fallback",
    model: "local",
  };
}

function isModelReadyCard(result?: LookupResult | null): boolean {
  return canUseWithoutRemote(result);
}

function lookupTraceSummary(result?: LookupResult | null) {
  return {
    word: result?.word ?? "",
    source: result?.source ?? "",
    model: result?.model ?? "",
    has_example: Boolean(result?.example?.trim()),
    example: result?.example ?? "",
    has_example_cn: Boolean(result?.example_cn?.trim()),
    meaning_len: result?.meaning_cn?.length ?? 0,
    trusted_model: isTrustedModelSource(result),
    fallback_source: isFallbackExampleSource(result),
    placeholder_example: isPlaceholderExample(result?.example),
    semantic_bad: result ? isSemanticallyBadExample(result.example, result.word, result.meaning_cn) : false,
    weak: isWeakLookupResult(result),
    displayable: hasDisplayableExample(result),
    model_ready: isModelReadyCard(result),
  };
}

export function EnglishVocabCoach() {
  const initialBook = React.useMemo(() => englishWordBooks[0], []);
  const [bookId, setBookId] = React.useState(initialBook.id);
  const [progress, setProgress] = React.useState<ProgressStore>({});
  const [currentWord, setCurrentWord] = React.useState(() => chooseWord(initialBook.words, {}, initialBook.id));
  const [revealed, setRevealed] = React.useState(false);
  const [reviewed, setReviewed] = React.useState(false);
  const [feedback, setFeedback] = React.useState(UI.readyHint);
  const [detail, setDetail] = React.useState<LookupResult | null>(null);
  const [detailLoading, setDetailLoading] = React.useState(false);
  const [detailError, setDetailError] = React.useState<string | null>(null);
  const [cardPreparing, setCardPreparing] = React.useState(false);
  const [preparingWord, setPreparingWord] = React.useState<string | null>(null);
  const [prefetchState, setPrefetchState] = React.useState<PrefetchUiState>("idle");
  const [selectedWord, setSelectedWord] = React.useState<LookupResult | null>(null);
  const [selectedLoading, setSelectedLoading] = React.useState<string | null>(null);
  const [selectedError, setSelectedError] = React.useState<string | null>(null);
  const [selectedStatus, setSelectedStatus] = React.useState<string | null>(null);
  const [activeExampleVariant, setActiveExampleVariant] = React.useState<ExampleVariant | null>(null);
  const lookupCacheRef = React.useRef<Record<string, LookupResult>>(readLookupCache());
  const bookIdRef = React.useRef(bookId);
  const currentWordRef = React.useRef(currentWord);
  const revealedRef = React.useRef(revealed);
  const detailRef = React.useRef<LookupResult | null>(detail);
  const cardTransitionRef = React.useRef(0);
  const lastExampleVariantIdRef = React.useRef<string | null>(null);
  const stateHydratedRef = React.useRef(false);
  const userInteractedRef = React.useRef(false);
  const exampleRetryRef = React.useRef<Record<string, number>>({});
  const activeBook = React.useMemo(() => getBookById(bookId), [bookId]);
  const currentMetadata = React.useMemo(() => getWordMetadata(activeBook, currentWord), [activeBook, currentWord]);
  const displayDetail = React.useMemo(() => {
    if (!detail) return null;
    const normalized = normalizeLookupResult(detail, activeBook);
    return hasDisplayableExample(normalized) ? normalized : null;
  }, [activeBook, detail]);
  const explicitExampleVariants = React.useMemo(
    () => (displayDetail && shouldUseExplicitVariants(displayDetail) ? buildExplicitExampleVariants(displayDetail, activeBook) : []),
    [activeBook, displayDetail],
  );
  const effectiveExample = activeExampleVariant?.example || displayDetail?.example || "";
  const effectiveExampleCn = activeExampleVariant?.example_cn || displayDetail?.example_cn || "";
  const currentCardReady = Boolean(currentWord && !detailLoading && !cardPreparing && effectiveExample);
  const cardBootstrapping = Boolean(!effectiveExample && detailLoading);

  React.useEffect(() => {
    if (!detail) {
      traceFrontend("detail_empty", { word: currentWord, book_id: activeBook.id, detail_loading: detailLoading });
      return;
    }
    traceFrontend(displayDetail ? "detail_displayable" : "detail_rejected_by_frontend", {
      book_id: activeBook.id,
      current_word: currentWord,
      detail_loading: detailLoading,
      ...lookupTraceSummary(detail),
    });
  }, [activeBook.id, currentWord, detail, displayDetail, detailLoading]);

  React.useEffect(() => {
    bookIdRef.current = bookId;
  }, [bookId]);

  React.useEffect(() => {
    currentWordRef.current = currentWord;
  }, [currentWord]);

  React.useEffect(() => {
    revealedRef.current = revealed;
  }, [revealed]);

  React.useEffect(() => {
    detailRef.current = detail;
  }, [detail]);

  React.useEffect(() => {
    const chosen = pickRandomVariantExcluding(explicitExampleVariants, lastExampleVariantIdRef.current);
    setActiveExampleVariant(chosen);
    lastExampleVariantIdRef.current = chosen?.id ?? null;
  }, [explicitExampleVariants, activeBook.id, currentWord]);

  React.useEffect(() => {
    void invoke("english_vocab_warmup").catch((error) => {
      console.warn("[EnglishVocab] warmup skipped:", error);
    });
  }, []);

  const applySharedState = React.useCallback((state: VocabState, preserveWord = true) => {
    const nextBook = getBookById(state.selected_book_id);
    const nextProgress = state.progress ?? {};
    const bookChanged = nextBook.id !== bookIdRef.current;
    const currentStillValid = nextBook.words.includes(currentWordRef.current);
    const firstHydration = !stateHydratedRef.current;
    setBookId(nextBook.id);
    setProgress(nextProgress);
    stateHydratedRef.current = true;
    if (
      (firstHydration && !userInteractedRef.current) ||
      (bookChanged && !revealedRef.current) ||
      (!preserveWord && !currentStillValid)
    ) {
      const keepCurrent = !firstHydration && currentStillValid ? currentWordRef.current : undefined;
      const nextWord = chooseWord(nextBook.words, nextProgress, nextBook.id, keepCurrent);
      currentWordRef.current = nextWord;
      setCurrentWord(nextWord);
      setRevealed(false);
      revealedRef.current = false;
      setReviewed(false);
      setSelectedWord(null);
      setSelectedError(null);
      setSelectedStatus(null);
      setFeedback(UI.readyHint);
    }
  }, []);

  React.useEffect(() => {
    let cancelled = false;
    const loadState = async (preserveWord = true) => {
      try {
        const state = await invoke<VocabState>("english_vocab_state_get");
        if (!cancelled) applySharedState(state, preserveWord);
      } catch (e) {
        console.warn("[EnglishVocab] shared state load failed:", e);
      }
    };
    void loadState(false);
    const id = window.setInterval(() => void loadState(true), 5000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [applySharedState]);

  const loadDetail = React.useCallback(
    async (
      word: string,
      contextSentence?: string,
      timeoutMs = REMOTE_LOOKUP_UI_TIMEOUT_MS,
      forceRemote = false,
      requireFinalExample = false,
    ) => {
      const key = lookupCacheKey(activeBook.id, word, contextSentence);
      const pendingKey = `${key}:${requireFinalExample ? "final" : "normal"}`;
      const cached = lookupCacheRef.current[key];
      const local = localLookupResult(word, activeBook);
      if (!forceRemote && cached) {
        const preferred = preferLookupResult(local ?? cached, cached, activeBook);
        if (preferred !== cached) cacheLookup(lookupCacheRef, key, preferred);
        if (!isWeakLookupResult(preferred)) return preferred;
      }
      const startRemoteLookup = () => {
        const pending = pendingLookups.get(pendingKey);
        if (pending) return pending;
        const promise = invoke<LookupResult>("english_vocab_lookup", {
          input: {
            word,
            book_id: activeBook.id,
            context_sentence: contextSentence,
            require_final_example: requireFinalExample,
          },
        }).finally(() => {
          pendingLookups.delete(pendingKey);
        });
        pendingLookups.set(pendingKey, promise);
        promise
          .then((result) => {
            traceFrontend("lookup_result_received", {
              book_id: activeBook.id,
              current_word: currentWordRef.current,
              requested_word: word,
              context_sentence: Boolean(contextSentence),
              require_final_example: requireFinalExample,
              ...lookupTraceSummary(result),
            });
            const currentDetail = detailRef.current;
            const merged =
              !requireFinalExample && (result.source === "local_gguf" || result.source === "local_translate") && currentDetail
                ? {
                    ...currentDetail,
                    meaning_cn:
                      (result.meaning_cn || "").trim() || currentDetail.meaning_cn,
                    part_of_speech:
                      (result.part_of_speech || "").trim() !== "-" && result.part_of_speech
                        ? result.part_of_speech
                        : currentDetail.part_of_speech,
                    example: result.example || currentDetail.example,
                    example_cn: result.example_cn || currentDetail.example_cn,
                    source: result.source,
                    model: result.model,
                  }
                : result;
            const existing = lookupCacheRef.current[key] ?? currentDetail;
            const preferred = requireFinalExample ? merged : preferLookupResult(merged, existing, activeBook);
            traceFrontend("lookup_result_preferred", {
              book_id: activeBook.id,
              current_word: currentWordRef.current,
              requested_word: word,
              context_sentence: Boolean(contextSentence),
              require_final_example: requireFinalExample,
              ...lookupTraceSummary(preferred),
            });
            if (!isWeakLookupResult(preferred)) {
              cacheLookup(lookupCacheRef, key, preferred);
            }
            if (!contextSentence && currentWordRef.current === word && bookIdRef.current === activeBook.id) {
              if (requireFinalExample && isWeakLookupResult(preferred)) {
                traceFrontend("lookup_result_rejected_before_set", {
                  book_id: activeBook.id,
                  current_word: currentWordRef.current,
                  requested_word: word,
                  require_final_example: requireFinalExample,
                  ...lookupTraceSummary(preferred),
                });
                setDetailError(null);
              } else {
                traceFrontend("lookup_result_set_detail", {
                  book_id: activeBook.id,
                  current_word: currentWordRef.current,
                  requested_word: word,
                  require_final_example: requireFinalExample,
                  ...lookupTraceSummary(preferred),
                });
                setDetail((prev) => (requireFinalExample ? preferred : preferLookupResult(preferred, prev, activeBook)));
                setDetailError(null);
                setDetailLoading(false);
              }
            }
          })
          .catch((error) => console.warn("[EnglishVocab] background lookup skipped:", error));
        return promise;
      };
      if (!forceRemote && !requireFinalExample && local) {
        const preferred = preferLookupResult(local, cached, activeBook);
        if (!isWeakLookupResult(preferred)) {
          cacheLookup(lookupCacheRef, key, preferred);
          return preferred;
        }
        try {
          const result = await withTimeout(startRemoteLookup(), timeoutMs);
          const remotePreferred = preferLookupResult(result, preferred, activeBook);
          if (!isWeakLookupResult(remotePreferred)) {
            cacheLookup(lookupCacheRef, key, remotePreferred);
          }
          return remotePreferred;
        } catch (error) {
          console.warn("[EnglishVocab] remote lookup failed after local definition:", error);
          if (requireFinalExample) throw error;
          return preferred;
        }
      }
      let result: LookupResult;
      try {
        result = await withTimeout(startRemoteLookup(), timeoutMs);
      } catch (error) {
        console.warn("[EnglishVocab] remote lookup failed, using local fallback:", error);
        if (requireFinalExample) throw error;
        result = fallbackLookupResult(word, activeBook);
      }
      if (requireFinalExample && isWeakLookupResult(result)) {
        throw new Error("final example is not ready");
      }
      const preferred = preferLookupResult(result, cached, activeBook);
      if (!isWeakLookupResult(preferred)) {
        cacheLookup(lookupCacheRef, key, preferred);
      }
      return preferred;
    },
    [activeBook.id],
  );

  const immediateDisplayResult = React.useCallback(
    (word: string): LookupResult | null => {
      const clean = cleanToken(word) || word;
      const cached = lookupCacheRef.current[lookupCacheKey(activeBook.id, clean)];
      if (isModelReadyCard(cached)) return normalizeLookupResult(cached, activeBook);
      const local = localLookupResult(clean, activeBook);
      return local && isModelReadyCard(local) ? normalizeLookupResult(local, activeBook) : null;
    },
    [activeBook],
  );

  const prefetchWords = React.useCallback(
    (words: string[], reason: string) => {
      const unique = Array.from(new Set(words.map((word) => cleanToken(word) || word).filter(Boolean)));
      unique.forEach((word, offset) => {
        window.setTimeout(() => {
          traceFrontend("next_card_prefetch_started", { book_id: activeBook.id, target_word: word, reason, offset });
          void loadDetail(word, undefined, REMOTE_LOOKUP_UI_TIMEOUT_MS, true, true)
            .then((result) => {
              traceFrontend("next_card_prefetch_finished", {
                book_id: activeBook.id,
                target_word: word,
                reason,
                offset,
                ...lookupTraceSummary(result),
              });
            })
            .catch((error) => {
              traceFrontend("next_card_prefetch_failed", {
                book_id: activeBook.id,
                target_word: word,
                reason,
                offset,
                error: errorText(error) || String(error ?? ""),
              });
              console.warn("[EnglishVocab] word prefetch skipped:", word, error);
            });
        }, offset * 220);
      });
    },
    [activeBook.id, loadDetail],
  );

  React.useEffect(() => {
    let cancelled = false;
    const cached = lookupCacheRef.current[lookupCacheKey(activeBook.id, currentWord)];
    if (cached && isModelReadyCard(cached)) {
      setDetail(cached);
      setDetailError(null);
      setDetailLoading(false);
      return () => {
        cancelled = true;
      };
    }
    const seed = immediateDisplayResult(currentWord);
    setDetail(seed);
    setDetailError(null);
    setDetailLoading(!seed);
    void loadDetail(currentWord, undefined, REMOTE_LOOKUP_UI_TIMEOUT_MS, true, true)
      .then((result) => {
        if (!cancelled) {
          if (isModelReadyCard(result)) {
            setDetail(result);
            setDetailError(null);
          } else {
            setDetailError(null);
          }
        }
      })
      .catch((error) => {
        console.warn("[EnglishVocab] model example lookup failed:", error);
        if (!cancelled) setDetailError(null);
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeBook.id, currentWord, immediateDisplayResult, loadDetail]);

  React.useEffect(() => {
    if (!effectiveExample || detailLoading) return;
    const index = activeBook.words.indexOf(currentWord);
    if (index < 0 || activeBook.words.length <= 1) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      const upcoming = Array.from({ length: Math.min(UPCOMING_MODEL_PREFETCH_COUNT, activeBook.words.length - 1) }, (_, offset) => {
        const nextIndex = (index + offset + 1) % activeBook.words.length;
        return activeBook.words[nextIndex];
      }).filter(Boolean);
      if (!cancelled) prefetchWords(upcoming, "current_card_ready");
    }, 350);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [activeBook.id, activeBook.words, currentWord, detailLoading, effectiveExample, prefetchWords]);

  React.useEffect(() => {
    const index = activeBook.words.indexOf(currentWord);
    if (index < 0 || activeBook.words.length <= 1) return;
    const timer = window.setTimeout(() => {
      const upcoming = Array.from({ length: Math.min(UPCOMING_MODEL_PREFETCH_COUNT, activeBook.words.length - 1) }, (_, offset) => {
        const nextIndex = (index + offset + 1) % activeBook.words.length;
        return activeBook.words[nextIndex];
      }).filter(Boolean);
      prefetchWords(upcoming, "current_word_changed");
    }, 600);
    return () => window.clearTimeout(timer);
  }, [activeBook.id, activeBook.words, currentWord, prefetchWords]);

  const retryCurrent = React.useCallback(() => {
    setDetail(null);
    setDetailError(null);
    setDetailLoading(true);
    void loadDetail(currentWord, undefined, REMOTE_LOOKUP_UI_TIMEOUT_MS, true, true)
      .then((result) => {
        if (isModelReadyCard(result)) {
          setDetail(result);
          setDetailError(null);
        } else {
          setDetail(null);
          setDetailError(null);
        }
      })
      .catch((error) => {
        console.warn("[EnglishVocab] retry lookup failed:", error);
        setDetailError(null);
      })
      .finally(() => setDetailLoading(false));
  }, [currentWord, loadDetail]);

  React.useEffect(() => {
    if (effectiveExample || detailLoading || cardPreparing) return;
    const key = `${activeBook.id}:${currentWord}`;
    const tried = exampleRetryRef.current[key] ?? 0;
    if (tried >= CURRENT_CARD_RETRY_LIMIT) return;
    exampleRetryRef.current[key] = tried + 1;
    const timer = window.setTimeout(() => {
      setDetailError(null);
      setDetailLoading(true);
      void loadDetail(currentWord, undefined, REMOTE_LOOKUP_UI_TIMEOUT_MS, true, true)
        .then((result) => {
          if (isModelReadyCard(result)) {
            setDetail((prev) => preferLookupResult(result, prev, activeBook));
            setDetailError(null);
          }
        })
        .catch((error) => {
          console.warn("[EnglishVocab] example auto-refresh skipped:", error);
          setDetailError(null);
        })
        .finally(() => {
          setDetailLoading(false);
        });
    }, Math.min(900 + tried * 350, 3200));
    return () => window.clearTimeout(timer);
  }, [activeBook, cardPreparing, currentWord, detailLoading, effectiveExample, loadDetail]);

  React.useEffect(() => {
    if (!detail || detail.refresh_hint !== "background_ai_refresh" || detailLoading) return;
    const key = `${activeBook.id}:${currentWord}:ai-refresh`;
    const tried = exampleRetryRef.current[key] ?? 0;
    if (tried >= 1) return;
    exampleRetryRef.current[key] = tried + 1;
    const timer = window.setTimeout(() => {
      void loadDetail(currentWord, undefined, REMOTE_LOOKUP_UI_TIMEOUT_MS, true, true)
        .then((result) => {
          if (isModelReadyCard(result)) {
            setDetail((prev) => preferLookupResult(result, prev, activeBook));
            setDetailError(null);
          }
        })
        .catch((error) => {
          console.warn("[EnglishVocab] AI example refresh skipped:", error);
        });
    }, 3200 + tried * 2500);
    return () => window.clearTimeout(timer);
  }, [activeBook, currentWord, detail, detailLoading, loadDetail]);

  const lookupExampleToken = React.useCallback(
    (token: string) => {
      userInteractedRef.current = true;
      const word = cleanToken(token);
      if (!word) return;
      const local = localLookupResult(word, activeBook);
      setSelectedWord(null);
      setSelectedError(null);
      setSelectedStatus(UI.lookupLocal);
      setSelectedLoading(word);
      if (local && !isWeakLookupResult(local)) {
        setSelectedWord(normalizeLookupResult(local, activeBook));
        setSelectedStatus(UI.lookupLocalHit);
        setSelectedLoading(null);
        return;
      }
      const fallback = normalizeLookupResult(local ?? fallbackLookupResult(word, activeBook), activeBook);
      setSelectedWord(fallback);
      setSelectedStatus(UI.lookupBackground);
      void loadDetail(word, detail?.example, TOKEN_LOOKUP_UI_TIMEOUT_MS)
        .then((result) => {
          const normalized = normalizeLookupResult(result, activeBook);
          const preferred = preferLookupResult(normalized, fallback, activeBook);
          setSelectedWord(preferred);
          setSelectedStatus(lookupSourceLabel(preferred));
          setSelectedError(null);
        })
        .catch((error) => {
          console.warn("[EnglishVocab] token lookup failed:", error);
          const message = errorText(error) || UI.lookupUnavailable;
          setSelectedStatus(`${UI.lookupBackgroundFailed}：${message}`);
          setSelectedError(message);
        })
        .finally(() => setSelectedLoading(null));
    },
    [activeBook, detail?.example, loadDetail],
  );

  React.useEffect(() => {
    const sentence = detail?.example?.trim();
    if (!sentence) {
      setPrefetchState("idle");
      return;
    }
    let cancelled = false;
    const payload = {
      input: {
        sentence,
        book_id: activeBook.id,
        max_tokens: 14,
      },
    };
    const runClientPrefetchFallback = async () => {
      const tokens = Array.from(
        new Set(
          sentence
            .split(/[^A-Za-z']+/g)
            .map((x) => cleanToken(x))
            .filter((x) => x.length >= 2),
        ),
      ).slice(0, 14);
      for (const token of tokens) {
        if (cancelled) return;
        try {
          await loadDetail(token, sentence, TOKEN_LOOKUP_UI_TIMEOUT_MS);
        } catch {
          // 单词预取失败不影响其他词
        }
      }
    };

    const runPrefetch = async () => {
      try {
        const result = await invoke<PrefetchResult>("english_vocab_prefetch_sentence", payload);
        if (cancelled) return;
        if (!result.started) {
          setPrefetchState("ready");
          return;
        }
        setPrefetchState("prefetching");
        window.setTimeout(() => {
          if (!cancelled) setPrefetchState("ready");
        }, 900);
      } catch (error) {
        if (cancelled) return;
        console.warn("[EnglishVocab] sentence prefetch skipped:", error);
        const raw = String(error ?? "");
        const isCommandUnavailable =
          raw.includes("english_vocab_prefetch_sentence") ||
          raw.toLowerCase().includes("unknown command") ||
          raw.toLowerCase().includes("not found");
        if (isCommandUnavailable) {
          // Rust 命令未加载时自动回退到前端本地预取，避免用户感知异常。
          setPrefetchState("prefetching");
          await runClientPrefetchFallback();
          if (!cancelled) setPrefetchState("ready");
          return;
        }
        setPrefetchState("error");
      }
    };

    setPrefetchState("prefetching");
    void runPrefetch();

    return () => {
      cancelled = true;
    };
  }, [detail?.example, activeBook.id, loadDetail]);

  const renderExample = React.useCallback(
    (example: string) =>
      example.split(/([A-Za-z]+(?:'[A-Za-z]+)?)/g).map((part, index) => {
        if (!/^[A-Za-z]+(?:'[A-Za-z]+)?$/.test(part)) {
          return <span key={`${part}-${index}`}>{part}</span>;
        }
        return (
          <button
            key={`${part}-${index}`}
            className="inline rounded px-0.5 text-left text-cyan-100 underline decoration-cyan-300/30 underline-offset-2 transition hover:bg-cyan-300/10 hover:text-white"
            onClick={() => lookupExampleToken(part)}
          >
            {part}
          </button>
        );
      }),
    [lookupExampleToken],
  );

  const prepareWordForDisplay = React.useCallback(
    async (word: string) => {
      const transitionId = cardTransitionRef.current + 1;
      cardTransitionRef.current = transitionId;
      setCardPreparing(true);
      setPreparingWord(word);
      setDetailError(null);
      const seed = immediateDisplayResult(word);
      currentWordRef.current = word;
      setCurrentWord(word);
      setDetail(seed);
      setDetailLoading(!seed);
      setRevealed(false);
      revealedRef.current = false;
      setReviewed(false);
      setSelectedWord(null);
      setSelectedError(null);
      setSelectedStatus(null);
      setFeedback(seed ? UI.readyHint : UI.preparingCard);
      setCardPreparing(false);
      setPreparingWord(null);
      traceFrontend(seed ? "next_card_rendered_immediately" : "next_card_switched_before_example_ready", {
        book_id: activeBook.id,
        target_word: word,
        had_seed: Boolean(seed),
        ...(seed ? lookupTraceSummary(seed) : {}),
      });
      try {
        const result = await loadDetail(word, undefined, REMOTE_LOOKUP_UI_TIMEOUT_MS, true, true);
        if (cardTransitionRef.current !== transitionId) return false;
        if (!isModelReadyCard(result)) {
          throw new Error("model example is not ready");
        }
        currentWordRef.current = word;
        setCurrentWord(word);
        setDetail(result);
        setDetailError(null);
        setDetailLoading(false);
        setRevealed(false);
        revealedRef.current = false;
        setReviewed(false);
        setSelectedWord(null);
        setSelectedError(null);
        setSelectedStatus(null);
        setFeedback(UI.readyHint);
        traceFrontend("next_card_model_ready", {
          book_id: activeBook.id,
          target_word: word,
          ...lookupTraceSummary(result),
        });
        return true;
      } catch (error) {
        if (cardTransitionRef.current === transitionId) {
          console.warn("[EnglishVocab] next card preparation failed:", word, error);
          traceFrontend("next_card_model_failed_after_switch", {
            book_id: activeBook.id,
            word,
            had_seed: Boolean(seed),
            error: errorText(error) || String(error ?? ""),
          });
          setFeedback(`下一张 ${word} 暂未准备好，请重试。`);
          setDetailLoading(false);
          setDetailError(null);
          setFeedback(seed ? UI.readyHint : "例句正在生成，稍后会自动刷新。");
        }
        return Boolean(seed);
      } finally {
        if (cardTransitionRef.current === transitionId) {
          setCardPreparing(false);
          setPreparingWord(null);
        }
      }
    },
    [activeBook.id, immediateDisplayResult, loadDetail],
  );

  const moveNext = React.useCallback(
    (nextProgress = progress) => {
      userInteractedRef.current = true;
      const next = chooseWord(activeBook.words, nextProgress, activeBook.id, currentWord);
      setFeedback(UI.preparingCard);
      void prepareWordForDisplay(next);
    },
    [activeBook.id, activeBook.words, currentWord, prepareWordForDisplay, progress],
  );

  const answer = (rating: Rating) => {
    userInteractedRef.current = true;
    revealedRef.current = true;
    setRevealed(true);
    setReviewed(true);
    setSelectedWord(null);
    setSelectedError(null);
    setSelectedStatus(null);
    setFeedback(feedbackFor(rating));
    const currentIndex = activeBook.words.indexOf(currentWord);
    if (currentIndex >= 0 && activeBook.words.length > 1) {
      const upcoming = Array.from({ length: Math.min(UPCOMING_MODEL_PREFETCH_COUNT, activeBook.words.length - 1) }, (_, offset) => {
        const nextIndex = (currentIndex + offset + 1) % activeBook.words.length;
        return activeBook.words[nextIndex];
      }).filter(Boolean);
      prefetchWords(upcoming, "review_clicked");
    }
    void invoke<VocabState>("english_vocab_state_record_review", {
      input: {
        book_id: activeBook.id,
        word: currentWord,
        rating,
        day: todayKey(),
        now_ms: Date.now(),
      },
    })
      .then((state) => applySharedState(state, true))
      .catch((e) => console.warn("[EnglishVocab] record review failed:", e));
  };

  return (
    <div className="h-full w-full select-none bg-transparent p-2 text-slate-100">
      <div className="flex h-full min-h-0 flex-col overflow-hidden rounded-lg border border-cyan-300/25 bg-slate-950/95 shadow-2xl shadow-black/40 backdrop-blur-xl">
        <div data-tauri-drag-region className="flex items-center justify-between border-b border-white/10 px-3 py-1.5">
          <div className="flex min-w-0 items-center gap-2">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-cyan-400/15 text-cyan-200">
              <BookOpen className="h-4 w-4" />
            </span>
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold tracking-normal">{UI.title}</div>
              <div className="truncate text-[11px] text-slate-400">{UI.subtitle}</div>
            </div>
          </div>
          <button
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-slate-400 transition hover:bg-white/10 hover:text-white"
            title={UI.close}
            onClick={() => void invoke("hide_english_vocab_window")}
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex min-h-0 flex-1 flex-col px-4 py-3">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="text-[11px] uppercase tracking-[0.16em] text-emerald-300/80">{UI.sectionTitle}</div>
              <div className="mt-0.5 truncate text-2xl font-semibold tracking-normal text-white">
                {cardBootstrapping ? "准备中" : currentWord}
              </div>
              <div className="mt-1 flex min-h-5 items-center gap-1 text-sm text-cyan-100/80">
                {cardBootstrapping ? (
                  <span className="text-xs text-slate-400">{UI.preparingCard}</span>
                ) : (
                  <>
                    <span>{choosePhonetic(currentMetadata?.phonetic, displayDetail?.phonetic || detail?.phonetic)}</span>
                    <span className="text-slate-500">{currentMetadata?.partOfSpeech || displayDetail?.part_of_speech || detail?.part_of_speech || ""}</span>
                  </>
                )}
              </div>
              {revealed ? (
                <div className="mt-2 min-h-8">
                  {detailLoading && !displayDetail ? (
                    <div className="text-xs text-slate-300">
                      {UI.loading}
                    </div>
                  ) : detailError && !displayDetail ? (
                    <div className="space-y-2 text-xs text-rose-100">
                      <div>{UI.lookupUnavailable}</div>
                      <button
                        className="rounded-md border border-cyan-300/20 px-2 py-1 text-cyan-100 transition hover:bg-cyan-400/10"
                        onClick={retryCurrent}
                      >
                        {UI.retry}
                      </button>
                    </div>
                  ) : displayDetail ? (
                    <div>
                      <div className="text-[11px] text-slate-500">{UI.meaningTitle}</div>
                      <div className="mt-0.5 text-sm font-semibold leading-snug text-emerald-100">
                        {userFacingMeaning(displayDetail.meaning_cn)}
                      </div>
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>
          </div>

          <div className="relative mt-3 min-h-[156px] max-h-[320px] overflow-y-auto rounded-md border border-white/10 bg-white/[0.04] px-3 py-2">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
                <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-cyan-200/80">{UI.exampleTitle}</div>
                <div className="mt-1 text-sm leading-relaxed text-slate-100">
                  {cardBootstrapping ? (
                    <span className="text-xs text-slate-400">
                      {UI.preparingCard}
                    </span>
                  ) : effectiveExample ? (
                    renderExample(effectiveExample)
                  ) : detailError ? (
                    <span className="text-xs text-slate-400">
                      {UI.examplePending}
                    </span>
                  ) : detailError || detail ? (
                    <span className="text-xs text-slate-400">
                      {UI.examplePending}
                    </span>
                  ) : (
                    <span className="text-xs text-slate-400">
                      {UI.exampleLoading}
                    </span>
                  )}
                </div>
                {revealed && effectiveExampleCn ? (
                  <div className="mt-2 rounded-md border border-white/10 bg-slate-950/35 px-2 py-1.5">
                    <div className="text-[11px] text-slate-500">{UI.sentenceMeaningTitle}</div>
                    <div className="mt-0.5 text-xs leading-relaxed text-slate-300">{effectiveExampleCn}</div>
                  </div>
                ) : null}
              </div>
              <button
                className="flex h-8 shrink-0 items-center gap-1 rounded-md border border-cyan-300/25 px-2 text-xs font-medium text-cyan-100 transition hover:bg-cyan-400/10 disabled:cursor-not-allowed disabled:opacity-45"
                disabled={cardPreparing || (!currentCardReady && !detailError)}
                onClick={() => {
                  userInteractedRef.current = true;
                  setRevealed(true);
                  setSelectedWord(null);
                  setSelectedError(null);
                  setSelectedLoading(null);
                  setSelectedStatus(null);
                  setFeedback(UI.revealOnly);
                  if (detailError) retryCurrent();
                }}
              >
                <Eye className="h-3.5 w-3.5" />
                {UI.reveal}
              </button>
            </div>

            {selectedLoading || selectedError || selectedWord ? (
              <div className="absolute left-3 right-3 top-12 z-10 rounded-md border border-cyan-300/25 bg-slate-900/95 p-2 shadow-xl shadow-black/40">
                <div className="flex items-center justify-between gap-2">
                  <div className="truncate text-sm font-semibold text-cyan-100">
                    {selectedWord?.word || selectedLoading || UI.loading}
                    <span className="ml-2 text-[11px] font-normal text-slate-400">
                      {choosePhonetic(selectedWord?.phonetic, getWordMetadata(activeBook, selectedWord?.word || selectedLoading || "")?.phonetic)}
                      {selectedWord?.part_of_speech ? ` ${selectedWord.part_of_speech}` : ""}
                    </span>
                  </div>
                  <button
                    className="flex h-6 w-6 shrink-0 items-center justify-center rounded text-slate-400 transition hover:bg-white/10 hover:text-white"
                    onClick={() => {
                      setSelectedWord(null);
                      setSelectedError(null);
                      setSelectedLoading(null);
                      setSelectedStatus(null);
                    }}
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
                <div className="mt-1 text-xs leading-relaxed text-slate-200">
                  {userFacingMeaning(selectedWord?.meaning_cn) || (selectedLoading ? UI.loading : selectedError ? UI.lookupUnavailable : "")}
                </div>
                {selectedStatus ? (
                  <div className="mt-1 border-t border-white/10 pt-1 text-[11px] leading-relaxed text-slate-400">
                    {selectedStatus}
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>

          <div className="mt-1 min-h-4 truncate text-[11px] text-slate-500">{feedback}</div>

          {!reviewed ? (
            <div className="mt-2 grid grid-cols-3 gap-2">
              <button
                className="flex h-9 items-center justify-center rounded-md border border-rose-300/25 bg-rose-400/10 text-sm text-rose-100 transition hover:bg-rose-400/20 disabled:cursor-not-allowed disabled:opacity-45"
                disabled={!currentCardReady}
                onClick={() => answer("unknown")}
              >
                {UI.unknown}
              </button>
              <button
                className="flex h-9 items-center justify-center rounded-md border border-amber-300/25 bg-amber-300/10 text-sm text-amber-100 transition hover:bg-amber-300/20 disabled:cursor-not-allowed disabled:opacity-45"
                disabled={!currentCardReady}
                onClick={() => answer("fuzzy")}
              >
                {UI.fuzzy}
              </button>
              <button
                className="flex h-9 items-center justify-center gap-1 rounded-md bg-emerald-400 text-sm font-semibold text-slate-950 transition hover:bg-emerald-300 disabled:cursor-not-allowed disabled:opacity-45"
                disabled={!currentCardReady}
                onClick={() => answer("known")}
              >
                <Check className="h-4 w-4" />
                {UI.know}
              </button>
            </div>
          ) : (
            <button
              className="mt-2 flex h-9 items-center justify-center gap-1 rounded-md bg-cyan-300 text-sm font-semibold text-slate-950 transition hover:bg-cyan-200 disabled:cursor-not-allowed disabled:opacity-45"
              disabled={cardPreparing}
              onClick={() => moveNext()}
            >
              {cardPreparing ? (preparingWord ? `正在准备 ${preparingWord}` : UI.preparingCard) : UI.next}
              <ChevronRight className="h-4 w-4" />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
