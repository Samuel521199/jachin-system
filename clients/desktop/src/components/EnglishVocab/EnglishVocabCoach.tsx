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
};

type ProgressStore = Record<string, WordProgress>;

const LOOKUP_CACHE_KEY = "jachin.english_vocab.lookup_cache.v6";
const REMOTE_LOOKUP_UI_TIMEOUT_MS = 12000;
const TOKEN_LOOKUP_UI_TIMEOUT_MS = 12000;
const pendingLookups = new Map<string, Promise<LookupResult>>();

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
  exampleLoading: "\u6b63\u5728\u51c6\u5907\u4f8b\u53e5...",
  retry: "\u91cd\u8bd5",
  lookupUnavailable: "\u672c\u5730\u8bcd\u5178\u6682\u672a\u547d\u4e2d\uff0c\u8bf7\u70b9\u51fb\u91cd\u8bd5\u6216\u5207\u6362\u4e0b\u4e00\u4e2a\u5355\u8bcd\u3002",
  prefetching: "\u8bcd\u4e49\u9884\u53d6\u4e2d",
  prefetchReady: "\u8bcd\u4e49\u5df2\u5c31\u7eea",
  prefetchError: "\u9884\u53d6\u5f02\u5e38",
  sectionTitle: "\u4eca\u65e5\u5355\u8bcd",
  exampleTitle: "\u82f1\u6587\u4f8b\u53e5",
  meaningTitle: "\u91ca\u4e49",
  sentenceMeaningTitle: "\u4f8b\u53e5\u4e2d\u6587",
  fallbackMeaning: "\u672c\u5730\u8bcd\u5178\u6682\u672a\u6536\u5f55\u8be5\u8bcd\u3002",
};

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
    text.includes("useful in everyday conversation")
  );
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

function makeExample(word: string, book: EnglishWordBook): ExamplePair {
  const curated = curatedExamples[word.toLowerCase()];
  if (curated) return curated;
  return { example: "", example_cn: "" };
}

function normalizeLookupResult(result: LookupResult, book: EnglishWordBook): LookupResult {
  if (result.source === "local_ecdict_definition") return result;
  if (!isPlaceholderExample(result.example)) return result;
  const pair = makeExample(result.word || "", book);
  return {
    ...result,
    example: pair.example,
    example_cn: pair.example_cn,
  };
}

function isWeakLookupResult(result?: LookupResult | null) {
  if (!result) return true;
  const meaning = (result.meaning_cn || "").trim();
  return (
    result.source === "local_fallback" ||
    result.source === "local_context_fallback" ||
    result.source === "local_ecdict_definition" ||
    meaning.includes(UI.fallbackMeaning) ||
    meaning.includes("\u53ef\u5148\u6309\u4f8b\u53e5\u8bed\u5883\u8bb0\u5fc6") ||
    meaning.includes("\u540e\u53f0\u4f1a\u81ea\u52a8\u8865\u5168") ||
    isPlaceholderExample(result.example) ||
    !result.example_cn?.trim()
  );
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
  const pair = makeExample(key || word, book);
  return {
    word: key || word,
    phonetic: metadata?.phonetic ?? "-",
    part_of_speech: metadata?.partOfSpeech ?? "-",
    meaning_cn: `${key || word}\uff1a${UI.fallbackMeaning}`,
    example: pair.example,
    example_cn: pair.example_cn,
    source: "local_fallback",
    model: "local",
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
  const [prefetchState, setPrefetchState] = React.useState<PrefetchUiState>("idle");
  const [selectedWord, setSelectedWord] = React.useState<LookupResult | null>(null);
  const [selectedLoading, setSelectedLoading] = React.useState<string | null>(null);
  const [selectedError, setSelectedError] = React.useState<string | null>(null);
  const lookupCacheRef = React.useRef<Record<string, LookupResult>>(readLookupCache());
  const bookIdRef = React.useRef(bookId);
  const currentWordRef = React.useRef(currentWord);
  const revealedRef = React.useRef(revealed);
  const detailRef = React.useRef<LookupResult | null>(detail);
  const activeBook = React.useMemo(() => getBookById(bookId), [bookId]);
  const currentMetadata = React.useMemo(() => getWordMetadata(activeBook, currentWord), [activeBook, currentWord]);

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

  const applySharedState = React.useCallback((state: VocabState, preserveWord = true) => {
    const nextBook = getBookById(state.selected_book_id);
    const nextProgress = state.progress ?? {};
    const bookChanged = nextBook.id !== bookIdRef.current;
    setBookId(nextBook.id);
    setProgress(nextProgress);
    if (!preserveWord || (bookChanged && !revealedRef.current)) {
      setCurrentWord(chooseWord(nextBook.words, nextProgress, nextBook.id, currentWordRef.current));
      setRevealed(false);
      setReviewed(false);
      setSelectedWord(null);
      setSelectedError(null);
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
    async (word: string, contextSentence?: string, timeoutMs = REMOTE_LOOKUP_UI_TIMEOUT_MS) => {
      const key = lookupCacheKey(activeBook.id, word, contextSentence);
      const cached = lookupCacheRef.current[key];
      const local = localLookupResult(word, activeBook);
      if (cached) {
        const preferred = preferLookupResult(local ?? cached, cached, activeBook);
        if (preferred !== cached) cacheLookup(lookupCacheRef, key, preferred);
        if (!isWeakLookupResult(preferred)) return preferred;
      }
      const startRemoteLookup = () => {
        const pending = pendingLookups.get(key);
        if (pending) return pending;
        const promise = invoke<LookupResult>("english_vocab_lookup", {
          input: {
            word,
            book_id: activeBook.id,
            context_sentence: contextSentence,
          },
        }).finally(() => {
          pendingLookups.delete(key);
        });
        pendingLookups.set(key, promise);
        promise
          .then((result) => {
            const currentDetail = detailRef.current;
            const merged =
              (result.source === "local_gguf" || result.source === "local_translate") && currentDetail
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
            const preferred = preferLookupResult(merged, existing, activeBook);
            cacheLookup(lookupCacheRef, key, preferred);
            if (!contextSentence && currentWordRef.current === word && bookIdRef.current === activeBook.id) {
              setDetail((prev) => preferLookupResult(preferred, prev, activeBook));
              setDetailError(null);
              setDetailLoading(false);
            }
          })
          .catch((error) => console.warn("[EnglishVocab] background lookup skipped:", error));
        return promise;
      };
      if (local) {
        const preferred = preferLookupResult(local, cached, activeBook);
        if (!isWeakLookupResult(preferred)) {
          cacheLookup(lookupCacheRef, key, preferred);
          void startRemoteLookup();
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
          return preferred;
        }
      }
      let result: LookupResult;
      try {
        result = await withTimeout(startRemoteLookup(), timeoutMs);
      } catch (error) {
        console.warn("[EnglishVocab] remote lookup failed, using local fallback:", error);
        result = fallbackLookupResult(word, activeBook);
      }
      const preferred = preferLookupResult(result, cached, activeBook);
      cacheLookup(lookupCacheRef, key, preferred);
      return preferred;
    },
    [activeBook.id],
  );

  React.useEffect(() => {
    let cancelled = false;
    const cached = lookupCacheRef.current[lookupCacheKey(activeBook.id, currentWord)];
    const local = localLookupResult(currentWord, activeBook);
    const preferred = local ? preferLookupResult(local, cached, activeBook) : cached;
    if (preferred && !isWeakLookupResult(preferred)) {
      setDetail(preferLookupResult(preferred, cached, activeBook));
      setDetailError(null);
      setDetailLoading(false);
      return () => {
        cancelled = true;
      };
    }
    setDetail(null);
    setDetailError(null);
    setDetailLoading(true);
    void loadDetail(currentWord)
      .then((result) => {
        if (!cancelled) setDetail(result);
      })
      .catch((error) => {
        if (!cancelled) setDetailError(errorText(error));
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeBook.id, currentWord, loadDetail]);

  const retryCurrent = React.useCallback(() => {
    setDetail(null);
    setDetailError(null);
    setDetailLoading(true);
    void loadDetail(currentWord)
      .then(setDetail)
      .catch((error) => setDetailError(errorText(error)))
      .finally(() => setDetailLoading(false));
  }, [currentWord, loadDetail]);

  const lookupExampleToken = React.useCallback(
    (token: string) => {
      const word = cleanToken(token);
      if (!word) return;
      const local = localLookupResult(word, activeBook);
      setSelectedWord(null);
      setSelectedError(null);
      if (local && !isWeakLookupResult(local)) {
        setSelectedLoading(null);
        setSelectedWord(normalizeLookupResult(local, activeBook));
        return;
      }
      const fallback = normalizeLookupResult(local ?? fallbackLookupResult(word, activeBook), activeBook);
      setSelectedWord(fallback);
      setSelectedLoading(word);
      void loadDetail(word, detail?.example, TOKEN_LOOKUP_UI_TIMEOUT_MS)
        .then((result) => {
          setSelectedWord((current) => preferLookupResult(result, current ?? fallback, activeBook));
          setSelectedError(null);
        })
        .catch((error) => {
          console.warn("[EnglishVocab] token lookup failed:", error);
          setSelectedError(errorText(error));
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

  const moveNext = React.useCallback(
    (nextProgress = progress) => {
      const next = chooseWord(activeBook.words, nextProgress, activeBook.id, currentWord);
      setCurrentWord(next);
      setRevealed(false);
      setReviewed(false);
      setSelectedWord(null);
      setSelectedError(null);
      setFeedback(UI.readyHint);
    },
    [activeBook.id, activeBook.words, currentWord, progress],
  );

  const answer = (rating: Rating) => {
    setRevealed(true);
    setReviewed(true);
    setSelectedWord(null);
    setSelectedError(null);
    setFeedback(feedbackFor(rating));
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
              <div className="mt-0.5 truncate text-2xl font-semibold tracking-normal text-white">{currentWord}</div>
              <div className="mt-1 flex min-h-5 items-center gap-1 text-sm text-cyan-100/80">
                {detailLoading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
                <span>{choosePhonetic(currentMetadata?.phonetic, detail?.phonetic)}</span>
                <span className="text-slate-500">{currentMetadata?.partOfSpeech || detail?.part_of_speech || ""}</span>
              </div>
              {revealed ? (
                <div className="mt-2 min-h-8">
                  {detailLoading && !detail ? (
                    <div className="flex items-center gap-2 text-xs text-slate-300">
                      <Loader2 className="h-3.5 w-3.5 animate-spin text-cyan-200" />
                      {UI.loading}
                    </div>
                  ) : detailError && !detail ? (
                    <div className="space-y-2 text-xs text-rose-100">
                      <div>{UI.lookupUnavailable}</div>
                      <button
                        className="rounded-md border border-cyan-300/20 px-2 py-1 text-cyan-100 transition hover:bg-cyan-400/10"
                        onClick={retryCurrent}
                      >
                        {UI.retry}
                      </button>
                    </div>
                  ) : detail ? (
                    <div>
                      <div className="text-[11px] text-slate-500">{UI.meaningTitle}</div>
                      <div className="mt-0.5 text-sm font-semibold leading-snug text-emerald-100">{detail.meaning_cn}</div>
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>
          </div>

          <div className="relative mt-3 min-h-[128px] max-h-[164px] overflow-y-auto rounded-md border border-white/10 bg-white/[0.04] px-3 py-2">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
                <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-cyan-200/80">{UI.exampleTitle}</div>
                <div className="mt-1 text-sm leading-relaxed text-slate-100">
                  {detailLoading && !detail ? (
                    <span className="inline-flex items-center gap-2 text-xs text-slate-400">
                      <Loader2 className="h-3.5 w-3.5 animate-spin text-cyan-200" />
                      {UI.exampleLoading}
                    </span>
                  ) : detail ? (
                    renderExample(detail.example)
                  ) : detailError ? (
                    <span className="text-rose-100">{UI.lookupUnavailable}</span>
                  ) : null}
                </div>
                {revealed && detail?.example_cn ? (
                  <div className="mt-2 rounded-md border border-white/10 bg-slate-950/35 px-2 py-1.5">
                    <div className="text-[11px] text-slate-500">{UI.sentenceMeaningTitle}</div>
                    <div className="mt-0.5 text-xs leading-relaxed text-slate-300">{detail.example_cn}</div>
                  </div>
                ) : null}
              </div>
              <button
                className="flex h-8 shrink-0 items-center gap-1 rounded-md border border-cyan-300/25 px-2 text-xs font-medium text-cyan-100 transition hover:bg-cyan-400/10"
                onClick={() => {
                  setRevealed(true);
                  setSelectedWord(null);
                  setSelectedError(null);
                  setSelectedLoading(null);
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
                    <span className="ml-2 text-[11px] font-normal text-slate-400">{selectedWord?.part_of_speech || ""}</span>
                  </div>
                  <button
                    className="flex h-6 w-6 shrink-0 items-center justify-center rounded text-slate-400 transition hover:bg-white/10 hover:text-white"
                    onClick={() => {
                      setSelectedWord(null);
                      setSelectedError(null);
                      setSelectedLoading(null);
                    }}
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
                <div className="mt-1 text-xs leading-relaxed text-slate-200">
                  {selectedWord?.meaning_cn || (selectedLoading ? UI.loading : selectedError ? UI.lookupUnavailable : "")}
                </div>
              </div>
            ) : null}
          </div>

          <div className="mt-1 min-h-4 truncate text-[11px] text-slate-500">{feedback}</div>

          {!reviewed ? (
            <div className="mt-2 grid grid-cols-3 gap-2">
              <button
                className="flex h-9 items-center justify-center rounded-md border border-rose-300/25 bg-rose-400/10 text-sm text-rose-100 transition hover:bg-rose-400/20"
                onClick={() => answer("unknown")}
              >
                {UI.unknown}
              </button>
              <button
                className="flex h-9 items-center justify-center rounded-md border border-amber-300/25 bg-amber-300/10 text-sm text-amber-100 transition hover:bg-amber-300/20"
                onClick={() => answer("fuzzy")}
              >
                {UI.fuzzy}
              </button>
              <button
                className="flex h-9 items-center justify-center gap-1 rounded-md bg-emerald-400 text-sm font-semibold text-slate-950 transition hover:bg-emerald-300"
                onClick={() => answer("known")}
              >
                <Check className="h-4 w-4" />
                {UI.know}
              </button>
            </div>
          ) : (
            <button
              className="mt-2 flex h-9 items-center justify-center gap-1 rounded-md bg-cyan-300 text-sm font-semibold text-slate-950 transition hover:bg-cyan-200"
              onClick={() => moveNext()}
            >
              {UI.next}
              <ChevronRight className="h-4 w-4" />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
