# 璇煶闄即鎬佹墽琛屾祦绋嬭鏄?
杩欎唤鏂囨。鐢ㄥ敖閲忔帴杩戜汉璇濈殑鏂瑰紡瑙ｉ噴锛氱敤鎴蜂粠鎸変笅璇煶鎸夐挳寮€濮嬶紝鍒?Jachin 璇村嚭璇煶鍥炲锛屼腑闂村埌搴曠粡杩囦簡鍝簺灞傘€佹瘡灞傝礋璐ｄ粈涔堛€佹剰鍥捐矾鐢辨€庝箞鍒ゆ柇銆佸摢浜涙ā鍨嬪弬涓庡伐浣溿€?
褰撳墠杩欏绯荤粺涓嶆槸涓€涓畝鍗曠殑鈥滆闊宠浆鏂囧瓧 -> 澶фā鍨?-> 鏂囧瓧杞闊斥€濄€傚畠鏇村儚涓€鏉″垎灞傛祦姘寸嚎锛?
```text
鐢ㄦ埛澹伴煶
  -> 褰曢煶 / 澹扮汗鍙€夎繃婊?  -> STT 璇煶璇嗗埆
  -> STT 鏂囨湰淇涓庣儹璇嶉闄╁垽鏂?  -> 鍓嶇璇煶鎰忓浘璺敱
  -> L3 WebSocket / HTTP 鍙戦€?  -> L3 蹇矾鐢?/ 鐩磋繛妯″瀷 / 瀹屾暣 Agent
  -> 鍓嶇娴佸紡鎺ユ敹鏂囧瓧
  -> 鍒嗗彞銆佹竻娲椼€佸幓閲?  -> JVS TTS 鍚堟垚
  -> 鎾斁闃熷垪
  -> 绯荤粺璇煶杈撳嚭
```

## 1. 鏍稿績杩涚▼鍜岀鍙?
褰撳墠璇煶闄即鎬佷富瑕佹湁涓夌被杩愯鍗曞厓銆?
### 妗岄潰鍓嶇

浣嶇疆澶ц嚧鍦細

```text
clients/desktop/src/chat.tsx
clients/desktop/src/voice/
```

瀹冭礋璐ｏ細

- 鎺ユ敹璇煶鎸夐挳銆丠UD銆丱rb銆佹ā鎷熻剼鏈緭鍏ャ€?- 璋冪敤鏈湴 JVS 鍋?STT銆?- 鍋氬墠绔剰鍥捐矾鐢便€?- 鎶婅矾鐢辩粨鏋滃寘瑁呮垚 `implicit_signals` 鍙戠粰 L3銆?- 鎺ユ敹 L3 鐨勬祦寮忔枃瀛椼€?- 鎶婃枃瀛楁媶鎴愰€傚悎鏈楄鐨勫彞瀛愩€?- 璋?JVS TTS 鍚堟垚璇煶銆?- 绠＄悊鎾斁闃熷垪鍜屾墦鏂€?
### JVS 璇煶鏈嶅姟

浣嶇疆锛?
```text
voice_server/main.py
voice_server/services/stt_service.py
voice_server/services/tts_service.py
voice_server/services/sv_service.py
```

榛樿绔彛锛?
```text
http://127.0.0.1:18982
```

瀹冭礋璐ｆ湰鍦伴煶棰戞ā鍨嬶細

- `/v1/stt/transcribe`锛氳闊宠瘑鍒€?- `/v1/tts/synthesize`锛氳闊冲悎鎴愩€?- `/v1/sv/filter_owner_track`锛氫富浜哄０绾硅建閬撹繃婊ゃ€?- `/v1/models/audio/warm`锛氶鐑?STT / TTS / SV銆?
### L3 鏅鸿兘灞?
浣嶇疆锛?
```text
l3_node/ws_server.py
l3_node/agent_core.py
```

榛樿 WebSocket锛?
```text
ws://127.0.0.1:18981
```

瀹冭礋璐ｏ細

- 鍒ゆ柇杩欎竴杞槸鍚﹀彲浠ヨ蛋璇煶 fast lane銆?- 璋冪敤杩滅鎴栨湰鍦?LLM銆?- 鍐冲畾鏄惁璺宠繃瀹屾暣璁板繂銆佹绱€佸伐鍏枫€佷换鍔￠摼璺€?- 瀵逛换鍔＄被璇锋眰杩涘叆瀹屾暣 agent / 宸ュ叿 / 浠诲姟璋冨害銆?- 鎶婂洖澶嶆祦寮忓洖浼犵粰鍓嶇銆?
## 2. 浠庣敤鎴疯璇濆埌 STT

鐢ㄦ埛鎸夎闊虫寜閽悗锛屽墠绔細鍏堟嬁鍒颁竴娈?WAV base64銆?
鍏ュ彛鍦?`chat.tsx` 鐨?`submitVoiceUtterance`銆?
澶ф娴佺▼鏄細

```text
鏀跺埌 wavBase64
  -> 寮€鍚?voice_chat trace
  -> 鍒ゆ柇鏄惁澶勪簬闄即鎬?UI
  -> 鍙€夋墽琛?owner-track 澹扮汗杩囨护
  -> 璋冪敤 transcribeWavBase64Detailed
  -> JVS /v1/stt/transcribe
  -> 杩斿洖鏈€缁堟枃鏈?```

### 澹扮汗杩囨护涓嶆槸姣忔閮藉己鍒惰窇

闄即鎬侀噷鏈変竴涓揩閫熸ā寮忥細

- 濡傛灉鐜榛樿璁や负姣旇緝瀹夐潤锛屽苟涓斿０绾逛弗鏍兼ā寮忔病寮€锛屽彲浠ヨ烦杩囦富浜鸿建杩囨护銆?- 濡傛灉璁剧疆瑕佹眰涓ユ牸锛屾墠浼氳皟鐢?`companion_filter_owner_track_wav`锛屽啀璧?JVS `/v1/sv/filter_owner_track`銆?
澹扮汗妯″瀷鏄?CAM++锛屽畠鍋氱殑浜嬫儏涓嶆槸璇嗗埆鏂囧瓧锛岃€屾槸鍒ゆ柇杩欎竴娈靛０闊冲儚涓嶅儚涓讳汉銆?
瀹冧細鎶婂師濮嬮煶棰戝垏鎴愮獥鍙ｏ紝渚嬪锛?
```text
step=250ms
len=900ms
high=0.38
low=0.25
```

鐒跺悗杈撳嚭锛?
```text
owner 娈?other 娈?璺宠繃鐗囨
涓讳汉杞ㄩ煶棰?```

涓讳汉杞ㄨ繃婊ゅ悗鐨勯煶棰戞墠浼氶€?STT銆傚畠鐨勭洰鏍囨槸鍑忓皯鏃佷汉鎻掕瘽锛屼絾鍓綔鐢ㄦ槸锛氬鏋滃垏鐗囪竟鐣屼笉鑷劧锛屽彲鑳藉壀鎺夎瘝澶磋瘝灏俱€?
### STT 妯″瀷

褰撳墠 JVS STT 鏄細

```text
sherpa-onnx Zipformer zh-en
```

鏈嶅姟绔枃浠讹細

```text
voice_server/services/stt_service.py
```

瀹冨仛涓変欢浜嬶細

1. 鎶婇煶棰戣В鐮佸苟閲嶉噰鏍峰埌 16k銆?2. 鐢?Sherpa-ONNX Zipformer 寰楀埌鍘熷璇嗗埆鏂囨湰銆?3. 缁忚繃 `VoiceUnderstandingCorrector` 鍋氶鍩熺籂閿欏拰鍒濇鐞嗚В銆?
杩斿洖缁撴灉閲屼笉鍙湁 `text`锛岃繕鏈夛細

```text
raw_text
corrected_text
confidence
duration_ms
backend
hotword_count
hotword_status
understanding
reply_plan
user_message
```

鍏朵腑 `reply_plan` 鍜?`user_message` 鐢ㄤ簬涓€绉嶇壒娈婃儏鍐碉細STT 灞傚凡缁忓垽鏂€滆繖涓闊充笉鑳界洿鎺ユ墽琛岋紝闇€瑕佽拷闂€濄€?
### STT 鐑瘝鏄粈涔?
鐑瘝鍙互鐞嗚В鎴愮粰 STT 鐨勨€滃姪鍚悕鍗曗€濄€?
鏅€氳闊宠瘑鍒ā鍨嬩笉涓€瀹氳璇嗕綘鐨勫伐浣滃満鏅€傛瘮濡備綘璇达細

```text
鎵撳紑 Lark
缁?Vivian 鍙戞秷鎭?鍒囧埌 VS Code
鎵撳紑 Codex
鐪嬩竴涓?Jachin 椤圭洰
```

濡傛灉娌℃湁鐑瘝锛屾ā鍨嬪彲鑳芥妸杩欎簺璇嶅惉鎴愶細

```text
Lark -> luck / lock / 鎷夊厠
Vivian -> vivi / 寰井瀹?/ 钖囪枃瀹?VS Code -> ws code / w s code
Codex -> code x / 鎵ｅ緱鍏嬫柉
Jachin -> jacking / 鍔犲嫟 / 鍢夐挦
```

鐑瘝鐨勪綔鐢ㄤ笉鏄€滃己琛屾敼瀛椻€濓紝鑰屾槸鍦?STT 瑙ｇ爜鏃跺憡璇夋ā鍨嬶細

```text
杩欎簺璇嶅湪褰撳墠绯荤粺閲屽緢甯歌銆?濡傛灉澹伴煶鏈夌偣鍍忓畠浠紝璇蜂紭鍏堣€冭檻瀹冧滑銆?```

鎵€浠ョ儹璇嶆洿鍍忊€滃惉鍐欐椂鏃佽竟鏀句簡涓€寮犲父鐢ㄤ汉鍚嶃€佸簲鐢ㄥ悕銆侀」鐩悕娓呭崟鈥濓紝涓嶆槸鍚庢湡鎶婃墍鏈夌浉浼艰瘝閮界矖鏆存浛鎹€?
### 褰撳墠鏈夊摢浜涚儹璇?
褰撳墠鐑瘝涓嶆槸鍐欐鍦ㄤ竴涓湴鏂癸紝鑰屾槸鐢?`SttHotwordProvider` 姹囨€汇€?
鏈嶅姟绔綅缃細

```text
voice_server/services/stt_hotwords.py
```

褰撳墠浼氫粠杩欎簺鏉ユ簮鍙栬瘝锛?
```text
l3_node.voice_entity_correction.export_hotwords()
data/voice/sherpa_hotwords.txt
data/voice/domain_lexicon.json
data/voice/stt_hotwords.json
config/voice_domain_lexicon.json
鐜鍙橀噺 JACHIN_STT_HOTWORDS
```

鎸夊綋鍓嶉」鐩噷鐨?snapshot锛岀儹璇嶆€绘暟澶х害鏄細

```text
155 涓?```

涓昏鍒嗘垚鍑犵被銆?
#### 搴旂敤 / 宸ュ叿鍚?
```text
Lark
椋炰功
Chrome
娴忚鍣?VS Code
vscode
Codex
```

瀹冧滑杩樺甫鏈夊父瑙佽鍚埆鍚嶏紝渚嬪锛?
```text
Lark: lark, feishu, flybook, luck, lock, 鎷夊厠, 鎷?Chrome: chrome, google chrome, clone, 娴忚鍣?VS Code: vs code, vscode, visual studio code, ws code, w s code
Codex: codex, code x, 鎵ｅ緱鍏嬫柉
```

#### 鑱旂郴浜哄悕瀛?
褰撳墠鑱旂郴浜虹儹璇嶉噷鍖呭惈锛?
```text
Vivian
Neil
Ethan
John
Berlith
Gordon
Nathan
Gavin
Daniel
Jade
Hex
Root
Seth
Buck
Cole
Jack Looi
Patrick
Jin
Lin
Samuel
Haku
Victor
Vigo
Mark
Jay
Max
Figo
Fincher
Anna
AnnaAnna
Lucy
Makoto
Musk
Elara
Summer
Jovi
Donnie
David
Rence
KK
Mariz
Tom
Reina
Mada
Stefan
Leslie
Hope
Germaine
```

鍏朵腑 Vivian銆丯eil銆丒than 杩欑被甯歌璇煶浠诲姟鐢ㄥ埌鐨勪汉鍚嶏紝浼氭湁鏇村鍒悕銆?
渚嬪 Vivian锛?
```text
Vivian
vivian
vivi
viian
vivan
vivien
钖囪枃瀹?寰井瀹?V钖?```

#### 椤圭洰 / 绯荤粺鍚?
```text
Jachin
jachin
jacking
鍔犲嫟
鍢夐挦
```

杩欑被鐑瘝涓昏甯姪璇嗗埆椤圭洰鍚嶃€佺郴缁熷悕锛岄伩鍏嶆妸 Jachin 鍚垚鍒殑鏅€氳嫳鏂囪瘝銆?
#### 鏉冮噸

鐑瘝閮芥湁鏉冮噸銆傛潈閲嶅彲浠ョ悊瑙ｆ垚鈥滄彁閱掑姏搴︹€濄€?
渚嬪褰撳墠姣旇緝閲嶈鐨勮瘝锛?
```text
Lark    25
Vivian  25
Jachin  20
椋炰功    20
钖囪枃瀹? 18
```

鏅€氳仈绯讳汉鍜屽父瑙佸埆鍚嶄竴鑸槸 10 鎴?20锛涗竴浜涘吋瀹瑰ぇ灏忓啓鐨勫疄楠岃瘝鍙兘鏄?4銆?銆?銆?銆?
鏉冮噸涓嶆槸瓒婇珮瓒婂ソ銆傚お楂樹細璁╂ā鍨嬭繃搴︾浉淇＄儹璇嶏紝鎶婁笉鐩稿叧鐨勫０闊充篃鍚垚鐑瘝銆?
### 鐑瘝濡備綍杈呬綈 STT

瀹屾暣娴佺▼澶ф鏄細

```text
褰曢煶闊抽
  -> STT 鏈嶅姟鏀跺埌闊抽
  -> SttHotwordProvider 姹囨€荤儹璇?  -> 鐢熸垚涓存椂 hotwords 鏂囦欢
  -> Sherpa-ONNX Zipformer 鐢?modified_beam_search 瑙ｇ爜
  -> 寰楀埌 raw_text
  -> VoiceUnderstandingCorrector 鍋氬疄浣撶籂閿欏拰鐞嗚В
  -> 杩斿洖 text / raw_text / hotword metadata
```

鏇翠汉璇濅竴鐐癸細

1. 姣忔璇嗗埆鍓嶏紝JVS 浼氭嬁涓€浠芥渶鏂扮儹璇嶆竻鍗曘€?2. 濡傛灉鐑瘝娓呭崟鍙樹簡锛孲TT recognizer 浼氶噸鏂板姞杞姐€?3. 鏈夌儹璇嶆椂锛孲herpa 浼氫粠鏅€?`greedy_search` 鏀规垚 `modified_beam_search`銆?4. 瀹冧細鍦ㄢ€滃涓彲鑳藉惉娉曗€濅箣闂达紝缁欑儹璇嶉偅鏉¤矾寰勫姞涓€鐐瑰€惧悜銆?5. 鏈€鍚庤緭鍑鸿瘑鍒枃鏈€?6. 杈撳嚭鍚庡啀杩涘疄浣撶籂閿欏眰锛屾妸涓婁笅鏂囬噷鐨勫埆鍚嶈鏁存垚鏍囧噯鍚嶅瓧銆?
#### 鈥滃涓彲鑳藉惉娉曗€濇槸鎬庝箞鏉ョ殑

STT 妯″瀷鍚０闊虫椂锛屼笉鏄儚浜轰竴鏍蜂竴娆℃€у惉鍑轰竴鍙ョ‘瀹氱殑璇濄€傛洿鎺ヨ繎涓嬮潰杩欎釜杩囩▼锛?
```text
澹伴煶鐗瑰緛杩涙ā鍨?  -> 姣忎竴灏忔闊抽閮藉彲鑳藉搴斿涓瓧 / 鎷奸煶 / token
  -> 瑙ｇ爜鍣ㄨ竟鍚竟淇濈暀鍑犳潯鍊欓€夎矾寰?  -> 姣忔潯璺緞閮芥湁涓€涓垎鏁?  -> 鍒嗘暟鏈€楂樼殑璺緞鎴愪负鏈€缁堣瘑鍒枃鏈?```

姣斿鐢ㄦ埛璇粹€滄墦寮€ Lark鈥濓紝澹伴煶姣旇緝绯婄殑鏃跺€欙紝妯″瀷鍐呴儴鍙兘鍚屾椂瑙夊緱杩欎簺閮借寰楅€氾細

```text
鎵撳紑 Lark
鎵撳紑 luck
鎵撳紑 lock
鎵撳紑鎷夊厠
鎵撳紑閭ｄ釜
```

濡傛灉涓嶇敤鐑瘝锛岀郴缁熷彲鑳藉彧璧版渶璐績鐨勪竴鏉¤矾锛氬綋鍓嶅摢涓€涓?token 鍒嗘渶楂橈紝灏变竴璺€変笅鍘汇€傝繖灏辨槸 `greedy_search`锛屼紭鐐规槸蹇紝缂虹偣鏄鏄撴棭鏃╅€夐敊銆?
鏈夌儹璇嶆椂锛屽綋鍓嶉厤缃細璁?Sherpa-ONNX Zipformer 浣跨敤锛?
```text
modified_beam_search
```

瀹冪殑鎰忔€濅笉鏄€滄妸鎵€鏈夊彲鑳藉彞瀛愰兘鍒楀嚭鏉ョ粰鎴戜滑鐪嬧€濓紝鑰屾槸妯″瀷瑙ｇ爜鏃跺唴閮ㄥ悓鏃朵繚鐣欏嚑鏉¤繕涓嶉敊鐨勫€欓€夎矾寰勩€傚綋鍓嶄唬鐮侀噷杩欎釜鏁伴噺鐢变笅闈㈠弬鏁版帶鍒讹細

```text
JACHIN_STT_MAX_ACTIVE_PATHS锛岄粯璁?4
```

鎵€浠ュ彲浠ョ矖鐣ョ悊瑙ｆ垚锛?
```text
涓嶇敤鐑瘝 / greedy_search锛氫竴璺線鍓嶇寽锛岀寽閿欎簡涓嶅お鍥炲ご
浣跨敤鐑瘝 / modified_beam_search锛氬悓鏃朵繚鐣欏嚑鏉″彲鑳藉惉娉曪紝鏈€鍚庡啀閫夋€诲垎鏈€楂樼殑涓€鏉?```

娉ㄦ剰锛氳繖浜涘€欓€夎矾寰勬槸 Sherpa 瑙ｇ爜鍣ㄥ唴閮ㄧ姸鎬侊紝褰撳墠 JVS 娌℃湁鎶娾€滃€欓€?1 / 鍊欓€?2 / 鍊欓€?3鈥濋兘杩斿洖缁欏墠绔€傚墠绔嬁鍒扮殑浠嶇劧鍙湁鏈€缁堣儨鍑虹殑 `raw_text`銆?
#### 鐑瘝鏉冮噸鍒板簳鎬庝箞褰卞搷閫夋嫨

JVS 浼氬厛鎶婄儹璇嶅啓鎴?Sherpa 鑳借鐨勪复鏃舵枃浠讹細

```text
Lark :25
Vivian :25
Jachin :20
...
```

鏂囦欢浣嶇疆閫氬父鏄郴缁熶复鏃剁洰褰曢噷鐨勶細

```text
jachin_sherpa_hotwords.txt
```

鐒跺悗 Sherpa 鍒濆鍖?recognizer 鏃朵細鎷垮埌锛?
```text
hotwords_file = 涓存椂鐑瘝鏂囦欢
hotwords_score = JACHIN_STT_HOTWORDS_SCORE锛岄粯璁?4.0
```

杩欓噷鏈変袱涓€滃姏搴︹€濓細

```text
璇嶈嚜宸辩殑 weight锛氫緥濡?Lark=25锛孷ivian=25
鍏ㄥ眬 hotwords_score锛氶粯璁?4.0
```

浜鸿瘽瑙ｉ噴灏辨槸锛?
```text
濡傛灉鏌愭潯鍊欓€夎矾寰勯噷鍑虹幇浜嗙儹璇嶏紝Sherpa 浼氱粰杩欐潯璺緞涓€鐐归澶栧姞鍒嗐€?鐑瘝鏉冮噸瓒婇珮銆佸叏灞€ hotwords_score 瓒婇珮锛岃繖涓姞鍒嗗€惧悜瓒婃槑鏄俱€?```

浣嗗畠涓嶆槸鏃犳潯浠舵浛鎹€傚畠涓嶄細鐪嬪埌鈥滄湁鐐瑰儚 Lark鈥濆氨涓€瀹氳緭鍑?Lark銆傛渶缁堣繕鏄鐪嬪０闊虫湰韬€佽瑷€妯″瀷鍒嗘暟銆佸€欓€夎矾寰勬€诲垎銆?
鎵€浠ュ畠鐨勬晥鏋滄洿鍍忥細

```text
鍘熸湰锛氭墦寮€ luck  51 鍒嗭紝鎵撳紑 Lark  49 鍒?-> 杈撳嚭 luck
鍔犵儹璇嶅悗锛氭墦寮€ luck 51 鍒嗭紝鎵撳紑 Lark  49 鍒?+ 鐑瘝鍔犳垚 -> 鍙兘杈撳嚭 Lark
```

杩欏氨鏄€滃湪澶氫釜鍙兘鍚硶涔嬮棿锛岀粰鐑瘝閭ｆ潯璺緞鍔犱竴鐐瑰€惧悜鈥濈殑鍏蜂綋鍚箟銆?
#### 鏈€鍚庤緭鍑鸿瘑鍒枃鏈槸鎬庝箞鍋氱殑

Sherpa 瑙ｇ爜缁撴潫鍚庯紝JVS 涓嶆槸鎷垮埌涓€鍫嗗€欓€夛紝鑰屾槸鎷垮埌涓€涓渶缁堟枃鏈細

```text
stream.result.text
```

鐒跺悗 JVS 浼氬仛涓€涓交娓呮礂锛?
```text
鍘绘帀澶氫綑绌虹櫧
鍘绘帀娌℃湁鎰忎箟鐨勭┖缁撴灉
寰楀埌 raw_text
```

鎺ョ潃杩涘叆锛?
```text
VoiceUnderstandingCorrector.correct(raw_text)
```

瀹冧細杈撳嚭锛?
```text
corrected_text
confidence
understanding
reply_plan
user_message
```

鏈€缁堝墠绔€氬父鐪嬪埌鐨勬槸锛?
```text
raw_text       鍘熷 STT 鏂囨湰
corrected_text 缁忚繃璇煶鐞嗚В灞傝鏁村悗鐨勬枃鏈?text           鏈€缁堢敤浜庡悗缁矾鐢辩殑鏂囨湰
```

#### 瀹炰綋绾犻敊灞傚叿浣撴寜浠€涔堣鍒欐敼鍚?
褰撳墠 JVS 鍚庡鐞嗙敤鐨勬槸锛?
```text
voice_server/services/voice_understanding.py
VoiceUnderstandingCorrector
```

瀹冨仛鐨勪笉鏄畝鍗曠殑鍏ㄥ眬鏇挎崲锛岃€屾槸鈥滃疄浣撹瘑鍒?+ 浠诲姟鐞嗚В鈥濄€傚ぇ姒傚垎鍥涙銆?
绗竴姝ワ紝鍔犺浇瀹炰綋搴撱€?
瀹炰綋搴撳寘鍚簲鐢ㄣ€佽仈绯讳汉銆侀」鐩細

```text
apps: Lark, Chrome, VS Code, Codex
contacts: Vivian, Neil, Ethan, ...
projects: Jachin
```

姣忎釜鏍囧噯鍚嶉兘鏈夊埆鍚嶏紝渚嬪锛?
```text
Lark: lark, feishu, flybook, luck, lock, 鎷夊厠
Vivian: vivian, vivi, 钖囪枃瀹? 寰井瀹?VS Code: vs code, vscode, visual studio code, ws code
Jachin: jachin, jacking, 鍔犲嫟, 鍢夐挦
```

绗簩姝ワ紝鍦ㄦ暣鍙ラ噷鎵弿鍙兘鐨勫疄浣撱€?
瀹冧細鐢ㄥ嚑绉嶇浉浼兼柟寮忔壘鍊欓€夛細

```text
瀹屽叏鐩稿悓锛歷ivian == Vivian
瀛愪覆鍖呭惈锛歡oogle chrome 閲屽寘鍚?chrome
瀛楃鐩镐技锛歷ivien 鍜?Vivian 寰堝儚
鎷奸煶鐩镐技锛氳枃钖囧畨 鍜?Vivian 瀵瑰簲鍚屼竴涓仈绯讳汉
鍙戦煶鎶樺彔锛氫竴浜?v/w銆乸h/f銆乧k/k 涔嬬被鐨勮繎浼间細鏀惧
```

姣忎釜鍊欓€変細鏈夊垎鏁板拰寮哄害锛?
```text
strong  寰堢‘瀹?medium  鏈夌偣鍍忥紝鍙互鍦ㄦ湁涓婁笅鏂囨椂浣跨敤
weak    澶急锛屼笉鑳界洿鎺ユ墽琛?```

绗笁姝ワ紝鐪嬭繖鍙ヨ瘽鏈夋病鏈夊姩浣滄剰鍥俱€?
绯荤粺浼氭鏌ュ姩浣滆瘝锛屼緥濡傦細

```text
鎵撳紑 / 鍚姩 / 鍒囧埌 / open
鎵惧埌 / 鎼滅储 / 鏌ユ壘 / find
鍙戦€?/ 鍙戞秷鎭?/ message / send
```

鐒跺悗鎶婂姩浣滃拰瀹炰綋缁勫悎璧锋潵銆?
姣斿锛?
```text
鎵撳紑 + Lark     -> open_app
缁?+ Vivian + 鍙戞秷鎭?-> send_message
鎵惧埌 + Neil     -> find_contact
Jachin + 椤圭洰   -> open_project 鎴栫浉鍏抽」鐩剰鍥?```

绗洓姝ワ紝鍐冲畾鑳戒笉鑳界洿鎺ヨ鏁存垚鏍囧噯鍚嶅瓧銆?
瑙勫垯澶ф鏄細

```text
濡傛灉瀹炰綋寰堝己锛岃€屼笖鍔ㄤ綔涔熸槑纭?-> 鍙互鎶婂埆鍚嶆崲鎴愭爣鍑嗗悕
濡傛灉瀹炰綋鏈夌偣鍍忥紝浣嗕笉澶熺‘瀹?-> 闇€瑕佺‘璁ゆ垨杩介棶
濡傛灉鏄彂娑堟伅锛屼絾缂鸿仈绯讳汉鎴栨秷鎭鏂?-> 涓嶇洿鎺ユ墽琛岋紝鐢熸垚杩介棶
濡傛灉鏁村彞璇濅笉鍍忎换鍔?-> 涓嶅己琛屾敼锛屽敖閲忎繚鐣欏師鏂囨湰
```

涓句緥锛?
```text
鍘熷 STT: 鎵撳紑鎷夊厠
瀹炰綋鍊欓€? 鎷夊厠 -> Lark锛屽己鍖归厤
鍔ㄤ綔: 鎵撳紑
缁撴灉: corrected_text = 鎵撳紑 Lark
```

```text
鍘熷 STT: 缁欒枃钖囧畨鍙戞秷鎭?瀹炰綋鍊欓€? 钖囪枃瀹?-> Vivian锛屽己鍖归厤
鍔ㄤ綔: 鍙戞秷鎭?缂哄け: 娑堟伅姝ｆ枃
缁撴灉: 涓嶇洿鎺ユ墽琛岋紝杩涘叆杩介棶锛氳鍙戠殑鍐呭鏄粈涔堬紵
```

```text
鍘熷 STT: 鎵句竴涓?vivien
瀹炰綋鍊欓€? vivien -> Vivian锛屼腑楂樼浉浼?鍔ㄤ綔: 鎵句竴涓?缁撴灉: 鍙兘瑙勬暣鎴?Vivian锛涘鏋滃垎鏁颁笉澶燂紝浼氳姹傜‘璁?```

```text
鍘熷 STT: 鎴戣寰?lark 杩欎釜璇嶆尯鎬?铏界劧鍑虹幇 Lark锛屼絾涓嶅儚浠诲姟鍔ㄤ綔
缁撴灉: 涓嶅簲璇ョ洿鎺ュ彉鎴愨€滄墦寮€ Lark鈥濇垨鎵ц浠诲姟
```

杩欏氨鏄负浠€涔堟枃妗ｉ噷璇粹€滆緭鍑哄悗鍐嶈繘瀹炰綋绾犻敊灞傦紝鎶婁笂涓嬫枃閲岀殑鍒悕瑙勬暣鎴愭爣鍑嗗悕瀛椻€濄€傚畠涓嶆槸鏃犺剳鏇挎崲锛岃€屾槸鍏堢湅锛?
```text
鍍忎笉鍍忓疄浣?鍍忎笉鍍忎换鍔?鍔ㄤ綔鏄惁鏄庣‘
妲戒綅鏄惁瀹屾暣
椋庨櫓鏄惁闇€瑕佺‘璁?```

杩欓噷鏈変袱灞備笉瑕佹贩鍦ㄤ竴璧凤細

```text
鐑瘝灞傦細甯姪 STT 鏇村鏄撳惉鍑?Lark / Vivian / Jachin
绾犻敊灞傦細鍦ㄢ€滄墦寮€/鍙戦€?鍒囧埌鈥濈瓑涓婁笅鏂囬噷锛屾妸鍒悕鏀规垚鏍囧噯瀹炰綋
```

涓句緥锛?
```text
鐢ㄦ埛璇达細甯垜鎵撳紑鎷夊厠
STT 鏈夌儹璇嶅悗鏇村鏄撳惉鍑猴細鎷夊厠
瀹炰綋绾犻敊鐪嬪埌鈥滄墦寮€ + 鎷夊厠鈥?鏈€缁堝彲鑳借鏁存垚锛氬府鎴戞墦寮€ Lark
```

鍐嶆瘮濡傦細

```text
鐢ㄦ埛璇达細缁欒枃钖囧畨鍙戞秷鎭?STT 鏈夌儹璇嶅悗鏇村鏄撳惉鍑猴細钖囪枃瀹?/ Vivian
瀹炰綋绾犻敊鐪嬪埌鈥滅粰 + 浜哄悕 + 鍙戞秷鎭€?鏈€缁堣鏁存垚锛氱粰 Vivian 鍙戞秷鎭?```

### 鐑瘝涓嶄細鍋氫粈涔?
鐑瘝涓嶆槸涓囪兘鐨勩€?
瀹冧笉浼氫繚璇侊細

```text
鍙璇翠簡灏变竴瀹氳瘑鍒纭?浠讳綍鐩镐技澹伴煶閮藉畨鍏ㄦ浛鎹?闀垮彞閲屾墍鏈夎嫳鏂囬兘璇诲噯
浠诲姟妲戒綅涓€瀹氬畬鏁?```

瀹冨彧鏄彁楂樻煇浜涜瘝琚€変腑鐨勬鐜囥€?
鎵€浠ョ儹璇嶆棦鑳芥晳璇嗗埆锛屼篃鍙兘甯︽潵鍓綔鐢ㄣ€?
鍏稿瀷鍓綔鐢ㄦ槸锛氱敤鎴疯浜嗕竴澶ф璇濓紝浣嗘ā鍨嬪洜涓虹儹璇嶅お寮猴紝鎶婃渶鍚庣粨鏋滃帇鎴愪竴涓緢鐭殑浠诲姟鍙ワ紝姣斿锛?
```text
鎵撳紑 Lark
缁?Vivian 鍙戞秷鎭?鍒囧埌 Chrome
```

杩欐椂绯荤粺灏辫鍒ゆ柇锛氳繖鍒板簳鏄湡瀹炴寚浠わ紝杩樻槸琚儹璇嶁€滃惛杩囧幓鈥濅簡銆?
## 3. STT 鍚庣殑瀹夊叏妫€鏌?
鍓嶇鎷垮埌 STT 缁撴灉鍚庯紝涓嶄細椹笂鍙戠粰 L3銆?
瀹冭繕浼氬仛鍑犵被妫€鏌ャ€?
### 绌烘枃鏈鏌?
濡傛灉 STT 娌¤瘑鍒嚭鏈夋晥涓枃銆佽嫳鏂囨垨鏁板瓧锛屼細鐩存帴鎶モ€滄湭鑳借瘑鍒闊冲唴瀹光€濄€?
### 鐑瘝姹℃煋妫€鏌?
濡傛灉 STT 鏄庢樉琚儹璇嶅甫鍋忥紝渚嬪璇嗗埆缁撴灉杩囧害璐磋繎鏌愪簺鐑瘝锛屽墠绔細鎷︽埅銆?
杩欎竴姝ュ湪鍓嶇锛?
```text
clients/desktop/src/chat.tsx
detectVoiceHotwordDomination(...)
```

瀹冧富瑕佺湅鍑犱釜淇″彿銆?
#### 1. 缁撴灉鏄笉鏄煭鐑瘝浠诲姟

渚嬪璇嗗埆缁撴灉閲屽嚭鐜帮細

```text
chrome
lark
vivian
neil
ethan
vscode
cursor
椋炰功
鎷夊厠
璋锋瓕
娴忚鍣?```

鍚屾椂鍙堟湁浠诲姟鍔ㄤ綔璇嶏細

```text
鎵撳紑
鍚姩
鎵惧埌
鍒囧埌
杩涘叆
缁?鍙?鍙戦€?娑堟伅
open
find
send
```

骞朵笖鏁村彞璇濆緢鐭紝灏变細琚爣璁版垚鈥滃彲鑳芥槸鐑瘝浠诲姟鈥濄€?
姣斿锛?
```text
鎵撳紑 Lark
缁?Vivian 鍙戞秷鎭?鍒囧埌 Chrome
```

杩欎簺閮藉睘浜庨珮椋庨櫓褰㈡€併€傚畠浠笉鏄竴瀹氶敊锛屼絾濡傛灉闊抽寰堥暱銆佺粨鏋滃嵈杩欎箞鐭紝灏辫璀︽儠銆?
#### 2. 褰曢煶寰堥暱锛屼絾璇嗗埆缁撴灉寰堢煭

濡傛灉鐢ㄦ埛褰曚簡 4.5 绉掍互涓婏紝鏈€鍚庡嵈鍙瘑鍒垚涓€涓緢鐭殑鐑瘝浠诲姟锛岀郴缁熶細璁や负鍙枒銆?
鍘熷洜寰堢畝鍗曪細

```text
鐢ㄦ埛璁蹭簡寰堜箙锛岀粨鏋滃彧鍓┾€滄墦寮€ Lark鈥?杩欏彲鑳戒笉鏄敤鎴风湡瀹炲畬鏁存剰鎬濓紝鑰屾槸鐑瘝鎶婅瘑鍒粨鏋滃惛鍋忎簡銆?```

瀵瑰簲鍘熷洜鍚嶏細

```text
short_hotword_task_from_long_audio
```

#### 3. 鐑瘝鏁伴噺寰堝ぇ锛屼笖缁撴灉姝ｅソ鏄煭浠诲姟

褰撳墠鐑瘝闆嗗悎澶х害 155 涓紝宸茬粡灞炰簬姣旇緝澶х殑涓婁笅鏂囧亸缃泦鍚堛€?
濡傛灉鐑瘝鏁伴噺瓒呰繃闃堝€硷紝骞朵笖璇嗗埆缁撴灉鍙堟槸寰堢煭鐨勭儹璇嶄换鍔★紝绯荤粺浼氭洿璋ㄦ厧銆?
瀵瑰簲鍘熷洜鍚嶏細

```text
large_hotword_set_short_task
```

浣嗘湁涓€涓緥澶栵細濡傛灉鏂囨湰鏄庢樉鏄暟瀛︽垨璁＄畻璇锋眰锛屾瘮濡傦細

```text
鎵撳紑璁＄畻鍣?涓€鍔犱竴绛変簬澶氬皯
绠椾竴涓?```

绯荤粺浼氶檷浣庣儹璇嶆薄鏌撳垽鏂紝閬垮厤鎶婃甯歌绠楃被璇锋眰璇嫤銆?
#### 4. 娴佸紡棰勮鍜屾渶缁?STT 鍐茬獊

濡傛灉鍓嶉潰娴佸紡棰勮鍚埌鐨勬槸涓€娈佃緝闀挎枃鏈紝浣嗘渶缁?STT 绐佺劧鍙樻垚寰堢煭鐨勭儹璇嶄换鍔★紝涔熶細鍙枒銆?
瀵瑰簲鍘熷洜鍚嶏細

```text
stream_final_conflict_hotword_task
```

#### 5. STT 涓嶆槸鏈€缁堢粨鏋?
濡傛灉鏉ユ簮鏄复鏃舵祦寮忚瘑鍒紝鎴栬€?`finalized=false`锛屼换鍔＄被璇锋眰涔熶細鏇磋皑鎱庛€?
瀵瑰簲鍘熷洜鍚嶏細

```text
non_final_stt_source
```

杩欑鎯呭喌涓嶄細鐩存帴鎵ц浠诲姟锛岃€屾槸鐢熸垚涓€鍙ョ‘璁わ細

```text
鎴戝垰鎵嶅惉鍒扮殑鏄€渪xx鈥濓紝浣嗚繖娈佃闊冲儚鏄鐑瘝褰卞搷浜嗐€?浣犲彲浠ュ啀璇翠竴閬嶏紝鎴栬€呯‘璁よ繖灏辨槸浣犺鍋氱殑鍚楋紵
```

鏈€缁堢敤鎴风湅鍒扮殑鏁堟灉灏辨槸锛?
```text
涓嶄細椹笂鎵撳紑杞欢 / 鍙戞秷鎭?/ 鎵ц浠诲姟
鑰屾槸鍏堥棶浣犵‘璁?```

杩欐槸涓€绉嶅畨鍏ㄥ埞杞︺€?
### 鐑瘝鏈€缁堜細甯︽潵浠€涔堟晥鏋?
鐞嗘兂鏁堟灉锛?
```text
鈥滄墦寮€鎷夊厠鈥?     -> 鏇村鏄撹瘑鍒苟瑙勬暣鎴?鈥滄墦寮€ Lark鈥?鈥滅粰钖囪枃瀹夊彂娑堟伅鈥?-> 鏇村鏄撹瘑鍒苟瑙勬暣鎴?鈥滅粰 Vivian 鍙戞秷鎭€?鈥滃垏鍒?vs code鈥? -> 鏇村鏄撹瘑鍒苟瑙勬暣鎴?鈥滃垏鍒?VS Code鈥?鈥渏achin 椤圭洰鈥?  -> 鏇村鏄撲繚鐣?Jachin
```

閬囧埌涓嶇‘瀹氭儏鍐垫椂锛?
```text
绯荤粺涓嶄細鐩存帴鎵ц
绯荤粺浼氭妸鍚埌鐨勫€欓€夊彞璇村嚭鏉ヨ浣犵‘璁?```

鏃ュ織閲岃兘鐪嬪埌锛?
```text
hotword_count
hotword_status
hotword_sources
hotwordDominated
hotwordDominationReasons
```

濡傛灉涓€杞闊宠鎷︽埅锛宍voice_chat.log` 閲屼細鍑虹幇锛?
```text
stt.hotword_dominated_blocked
```

杩欒鏄庣郴缁熶笉鏄€滄病鍚噦灏变贡鎵ц鈥濓紝鑰屾槸鍙戠幇璇嗗埆缁撴灉鍙兘琚儹璇嶅甫鍋忥紝鎵€浠ュ仠涓嬫潵闂綘銆?
### 杩介棶鐢熸垚

濡傛灉 STT / understanding 鍒ゆ柇缂烘Ы锛屾瘮濡傜敤鎴疯鈥滃府鎴戝彂娑堟伅鈥濓紝浣嗘病璇村彂缁欒皝銆佸彂浠€涔堬紝鍓嶇浼氳繘鍏ヨ拷闂敓鎴愯矾寰勩€?
杩欓噷鐨勮鍒欏眰鍙粰鍑?`ReplyPlan`锛岀湡姝ｈ缁欑敤鎴风殑璇濅細浜ょ粰涓€涓交閲?LLM composer 鍐欐垚鑷劧璇█銆?
涔熷氨鏄锛?
```text
瑙勫垯灞傦細鍒ゆ柇缂轰粈涔?LLM composer锛氭妸杩介棶璇村緱鍍忎汉璇?```

## 4. 鍓嶇鎰忓浘璺敱

鍓嶇璇煶璺敱鐨勫崟涓€浜嬪疄婧愭槸锛?
```text
clients/desktop/src/voice/voiceIntentRouter.ts
```

Python 閲岀殑锛?
```text
scripts/voice_intent_router.py
```

鍙槸涓轰簡 benchmark / 鑴氭湰娴嬭瘯鍘昏皟鐢ㄥ悓涓€涓?TypeScript 璺敱锛屼笉鏄浜屽璺敱銆?
### 璺敱杈撳嚭浠€涔?
璺敱鍣ㄨ緭鍑轰竴涓?`VoiceDispatcherDecision`锛屾牳蹇冨瓧娈垫湁锛?
```text
tier                 澶у眰绾э細闂茶亰 / 鐭换鍔?/ 闀夸换鍔?intent_class         鏇村叿浣撶殑鎰忓浘绫诲埆
execution_lane       鎵ц杞﹂亾
interrupt_verdict    濡傛灉宸叉湁浠诲姟锛屽垽鏂槸鏌ョ姸鎬併€佸彇娑堛€佷慨鏀广€佸苟琛岃繕鏄仮澶?router_hints         缁?L3 鐨勬彁绀?route_evidence       璺敱璇佹嵁
normalized_text      缁?L3 鐨勪慨姝ｆ枃妗?```

### 涓変釜 tier

褰撳墠鏈変笁灞傦細

```text
CHIT_CHAT   闂茶亰銆佽交闂瓟銆侀櫔浼磋繛鎺?SHORT_TASK  鐭换鍔★紝閫傚悎鍓嶅彴鍚屾澶勭悊
LONG_TASK   闀夸换鍔★紝閫傚悎鍚庡彴鎻愪氦
```

### intent_class

甯歌鍊硷細

```text
CHITCHAT       鏅€氶棽鑱?QUERY_LIGHT    杞婚棶绛旓紝渚嬪鈥滀粖澶╁悆浠€涔堚€?TASK_SYNC      鐭换鍔★紝渚嬪鈥滄墦寮€璁＄畻鍣ㄢ€?TASK_ASYNC     闀夸换鍔★紝渚嬪鈥滄妸鏁翠釜鐩綍鐢熸垚鎶ュ憡鈥?CONTROL        瀵瑰凡鏈変换鍔＄殑鎺у埗锛屼緥濡傚彇娑堛€佹煡杩涘害銆佷慨鏀?CLARIFY_REPLY  鐢ㄦ埛姝ｅ湪鍥炵瓟涓婁竴杞拷闂?AMBIGUOUS      澶ā绯婏紝闇€瑕佹緞娓?```

### execution_lane

杩欎釜瀛楁鍐冲畾鍚庨潰鎬庝箞璧帮細

```text
direct_llm          鐩存帴闂ā鍨嬶紝涓嶈繘瀹屾暣浠诲姟閾?foreground          鍓嶅彴鐭换鍔?background_submit   鍚庡彴闀夸换鍔℃彁浜?background_control  鎺у埗宸叉湁鍚庡彴浠诲姟
control_local       鏈湴鎺у埗
```

## 5. 褰撳墠鎰忓浘璺敱瑙勫垯

璺敱鏈川鏄€滆鍒欎富瀵?+ 缁欏ぇ妯″瀷鐣欒〃杈剧┖闂粹€濄€?
涔熷氨鏄锛屽畠涓嶆槸璁╁ぇ妯″瀷浠庨浂鍒ゆ柇涓€鍒囷紝鑰屾槸鍏堢敤瑙勫垯鎶婅竟鐣屽畾浣忥細

- 杩欐槸涓嶆槸浠诲姟锛?- 瑕佷笉瑕佹墽琛屽伐鍏凤紵
- 瑕佷笉瑕佽繘鍚庡彴锛?- 鑳戒笉鑳借烦杩囪蹇嗗拰妫€绱紵
- 鑳戒笉鑳界洿鎺ユā鏉垮洖澶嶏紵
- 鏈夋病鏈夋鍦ㄨ繍琛岀殑浠诲姟瑕佹帶鍒讹紵

浣嗘槸鍏蜂綋鍥炲鍐呭锛屽挨鍏舵槸闂茶亰鍜岃交闂瓟锛屼粛鐒剁敱 LLM 鏉ュ啓銆?
### 5.1 presence_template锛氬彧纭鈥滀綘鍦ㄤ笉鍦ㄢ€?
鍏稿瀷杈撳叆锛?
```text
浣犲ソ
鍦ㄥ悧
浣犲湪鍚?鍚緱鍒板悧
鍠?璇磋瘽
璁茶璇?```

璺敱缁撴灉锛?
```text
tier = CHIT_CHAT
intent_class = CHITCHAT
execution_lane = direct_llm
fast_lane = true
fast_lane_kind = presence_template
allow_template_reply = true
skip_context_retrieval = true
skip_context_sniffer = true
skip_experience_rag = true
skip_gateway_enrich = true
```

杩欑被璇锋眰鍏佽 L3 鐩存帴鐢ㄦā鏉垮洖绛旓細

```text
鎴戝湪銆?鍦ㄥ憿銆?鍚潃鍛€?```

瀹冪殑鐩爣鏄瀬蹇紝璁╃敤鎴风煡閬撶郴缁熸椿鐫€銆?
### 5.2 light_query锛氳交闂瓟锛屼絾涓嶈兘妯℃澘鏁疯

鍏稿瀷杈撳叆锛?
```text
浠婂ぉ鍚冧粈涔?浣犺寰楁垜浠婂ぉ鍚冧粈涔?鎴戣涓嶈鍠濆挅鍟?杩欎釜鎬庝箞閫?```

璺敱缁撴灉锛?
```text
tier = CHIT_CHAT
intent_class = QUERY_LIGHT
execution_lane = direct_llm
fast_lane = true
fast_lane_kind = light_query
allow_template_reply = false
```

鍏抽敭鐐规槸锛?
```text
light_query 涔熻蛋 fast lane锛屼絾绂佹妯℃澘鍥炲銆?```

鎵€浠モ€滀粖澶╁悆浠€涔堚€濅笉鑳藉啀鍥炵瓟鈥滄垜鍦ㄢ€濄€傚畠蹇呴』杩涘叆 direct LLM锛岃妯″瀷鍥炵瓟闂鏈韩銆?
### 5.3 chat_direct锛氭櫘閫氶棽鑱?
鍏稿瀷杈撳叆锛?
```text
闄垜鑱婅亰
鎴戜粖澶╂湁鐐圭疮
璋㈣阿
娌′簨
濂藉惂
```

璺敱缁撴灉閫氬父鏄細

```text
CHIT_CHAT + direct_llm + fast_lane
```

瀹冧細璺宠繃瀹屾暣涓婁笅鏂囨绱紝浣嗙敱妯″瀷鐢熸垚鑷劧鍥炲銆?
### 5.4 SHORT_TASK锛氱煭浠诲姟

鍏稿瀷杈撳叆锛?
```text
甯垜鎵撳紑璁＄畻鍣?鎻愰啋鎴戜笅鍗堝紑浼?鏌ヤ竴涓嬪ぉ姘?鎵撳紑 Chrome
```

璺敱缁撴灉锛?
```text
tier = SHORT_TASK
intent_class = TASK_SYNC
execution_lane = foreground
fast_lane = false
play_task_ack = true
```

鍓嶇浼氬厛鎾竴涓煭鎻愮ず锛屾瘮濡傦細

```text
鎴戞兂鎯炽€?```

鐒跺悗鎶婁换鍔′氦缁?L3 鐨勬甯搁摼璺€?
### 5.5 LONG_TASK锛氶暱浠诲姟

鍏稿瀷杈撳叆锛?
```text
鎶婃暣涓洰褰曠敓鎴愭姤鍛?鎵归噺鍒嗘瀽杩欎簺鏂囦欢
鎶婃墍鏈?md 鏂囨。閫愪釜鎽樿骞剁敓鎴愭姤鍛?```

璺敱缁撴灉锛?
```text
tier = LONG_TASK
intent_class = TASK_ASYNC
execution_lane = background_submit
force_background = true
acceptance_round = true
play_task_ack = true
hud_terminal = true
```

鍓嶇浼氬敖蹇粰鐢ㄦ埛涓€涓‘璁わ細

```text
鏀跺埌锛屾垜鏉ュ鐞嗐€?```

鐒跺悗璁?L3 / task engine 鍘诲鐞嗛暱浠诲姟銆?
### 5.6 CONTROL锛氭湁浠诲姟杩愯鏃剁殑鎺у埗璇煶

濡傛灉褰撳墠宸茬粡鏈?active task锛岀敤鎴疯锛?
```text
鍋滀竴涓?鍙栨秷
鍋氬埌鍝簡
杩涘害鎬庝箞鏍?鏀规垚杩欐牱
缁х画
```

璺敱浼氫紭鍏堣涓鸿繖鏄宸叉湁浠诲姟鐨勬帶鍒躲€?
鍙兘缁撴灉锛?
```text
interrupt_verdict = ABORT
interrupt_verdict = STATUS
interrupt_verdict = MODIFY
interrupt_verdict = RESUME
interrupt_verdict = PARALLEL
```

杩欏氨鏄€滀换鍔¤繕娌℃墽琛屽畬鏃剁敤鎴风户缁璇濃€濈殑涓昏璋冮厤鏈哄埗銆?
## 6. 鍓嶇濡備綍鎶婅矾鐢辩粨鏋滃彂缁?L3

璺敱瀹屾垚鍚庯紝鍓嶇涓嶄細鍙彂涓€鍙ユ枃鏈€?
瀹冧細鎶婄敤鎴峰師濮?STT銆佷慨姝ｅ悗鏂囨湰銆佽矾鐢辩粨鏋溿€乫ast lane 鏍囪銆佷换鍔′笂涓嬫枃涓€璧峰杩?`implicit_signals`銆?
澶ф鍖呮嫭锛?
```text
desktop_companion = true
voice_raw_stt_text
voice_asr_raw_text
voice_corrected_text
voice_final_text
voice_routed_text
voice_dispatcher_decision
voice_dispatch_tier
voice_intent_class
voice_dispatch_lane
voice_interrupt_verdict
voice_fast_lane
voice_fast_lane_kind
voice_allow_template_reply
voice_route_evidence
skip_context_retrieval
skip_context_sniffer
skip_experience_rag
skip_gateway_enrich
prefer_direct_llm
force_background
acceptance_round
inject_task_context
inject_light_task_context
light_task_context
target_task_id
task_context_summary
```

杩欏寘涓滆タ寰堥噸瑕併€傚畠鍛婅瘔 L3锛?
- 杩欏彞璇濆師濮?STT 鏄粈涔堛€?- 鍓嶇淇鍚庡噯澶囪妯″瀷鐪嬪埌浠€涔堛€?- 杩欐槸闂茶亰銆佽交闂瓟銆佺煭浠诲姟杩樻槸闀夸换鍔°€?- 瑕佷笉瑕佽烦杩囬噸閾捐矾銆?- 鑳戒笉鑳界敤妯℃澘銆?- 鏄惁鏈夊悗鍙颁换鍔℃鍦ㄨ窇銆?
## 7. L3 鏀跺埌璇煶鍚庣殑璺緞

L3 WebSocket 鍦細

```text
l3_node/ws_server.py
```

鏀跺埌娑堟伅鍚庯紝鍏堝仛涓€涓垽鏂細

```text
杩欐槸涓嶆槸 voice fast lane锛?```

### 7.1 presence_template 鐨勬渶蹇矾寰?
濡傛灉鏄?`presence_template`锛屽苟涓?`allow_template_reply = true`锛孡3 鍙互涓嶈皟澶фā鍨嬶紝鐩存帴杩斿洖妯℃澘銆?
杩欐潯璺緞鏈€蹇細

```text
鍓嶇璺敱
  -> L3 妯℃澘閫夋嫨
  -> chunk
  -> answer
  -> TTS
```

閫傚悎鈥滃湪鍚?浣犲ソ/鍚緱鍒板悧鈥濄€?
### 7.2 light_query / chat_direct 鐨?fast lane

濡傛灉鏄?`light_query` 鎴栨櫘閫氶棽鑱婏細

```text
voice_fast_lane = true
allow_template_reply = false
```

L3 浼氳蛋鐩磋繛妯″瀷锛?
```text
_voice_fast_lane_messages
  -> engine.generate_response_stream
  -> 1 鍒?2 鍙ョ煭鍥炲
```

杩欐潯璺緞璺宠繃锛?
- 瀹屾暣涓婁笅鏂囨绱€?- context sniffer銆?- gateway enrich銆?- experience RAG銆?- 宸ュ叿姹犲姞杞姐€?- ReAct 宸ュ叿寰幆銆?
浣嗘槸瀹冧粛鐒朵細璋冪敤 LLM锛屾墍浠ュ鏋滆繙绔ā鍨嬮 token 鍗′綇锛岀敤鎴疯繕鏄細瑙夊緱鎱€?
L3 杩樻湁涓€涓 token 瓒呮椂淇濇姢锛?
```text
JACHIN_VOICE_FAST_LANE_TIMEOUT_SEC 榛樿绾?1.4 绉?```

濡傛灉 presence ack 瓒呮椂锛屽彲浠ュ厹搴曗€滄垜鍦ㄣ€傗€濄€?
浣嗗鏋滄槸闈?presence 鐨勮交闂瓟锛屼笉鑳藉厹搴曗€滄垜鍦ㄢ€濓紝鍚﹀垯灏变細绛旈潪鎵€闂紝鎵€浠ヤ細鎶涚粰鍚庣画閾捐矾鎴栨姤閿欍€?
### 7.3 瀹屾暣 Agent 璺緞

濡傛灉涓嶆槸 fast lane锛屾垨鑰?fast lane 澶辫触锛屽氨杩涘叆锛?
```text
run_agent
```

瀹屾暣璺緞鍙兘鍖呭惈锛?
- 浼氳瘽鍘嗗彶銆?- Memory Nexus銆?- Intent Gateway銆?- output format signals銆?- direct_llm_bypass 鍒ゆ柇銆?- OOD veto銆?- DAG / subintent 鎷嗚В銆?- 宸ュ叿鍔犺浇銆?- ReAct 寰幆銆?- 鏈湴宸ュ叿鎵ц銆?- 闀夸换鍔¤皟搴︺€?- 璁板繂鍐欏叆銆?
杩欐潯璺緞鑳藉姏寮猴紝浣嗘參锛屼篃鏇村鏄撳嚭鐜扳€滅敤鎴峰彧鏄兂鑱婂ぉ锛岀郴缁熷嵈鍍忓湪鍋氶」鐩皟搴︹€濈殑鎰熻銆?
## 8. L3 閲屾ā鍨嬪浣曡繍浣?
### 蹇矾鐢辨ā鍨嬭皟鐢?
璇煶 fast lane 浼氭瀯閫犱竴涓潪甯哥煭鐨?system prompt銆?
瀹冨憡璇夋ā鍨嬶細

- 浣犳槸 Jachin 鐨勯櫔浼存€佽闊冲姪鎵嬨€?- 褰撳墠鏄闊抽棽鑱婂揩璺緞銆?- 鐩存帴銆佽嚜鐒躲€佹俯鏌斻€?- 涓枃鐭瓟锛? 鍒?2 鍙ャ€?- 涓嶈宸ュ叿銆?- 涓嶈灞曠ず鎺ㄧ悊銆?- 濡傛灉鏄交闂瓟锛屽繀椤诲洖绛旈棶棰樻湰韬紝涓嶈兘鍙鈥滄垜鍦ㄢ€濄€?
妯″瀷鍙傛暟澶ц嚧鍋忎繚瀹堬細

```text
temperature 绾?0.35
max_tokens 绾?80
```

鍙互閫氳繃鐜鍙橀噺鎸囧畾蹇矾鐢辨ā鍨嬶細

```text
JACHIN_VOICE_FAST_LANE_MODEL
```

### direct_llm_bypass

鍦?`agent_core.py` 閲岋紝濡傛灉鍒ゆ柇鍙互鐩磋繛妯″瀷锛屼細璧帮細

```text
_run_direct_llm_completion
```

濡傛灉鏄闊?fast lane锛屽畠浼氾細

- 绂佺敤瀹屾暣 ReAct銆?- 闄愬埗杈撳嚭 tokens銆?- 鎻愮ず涓嶈闀跨瘒銆?- 瀵?light_query 棰濆寮鸿皟涓嶈绛斺€滄垜鍦ㄢ€濄€?- 灏濊瘯鍏抽棴妯″瀷 thinking銆?
### 瀹屾暣 ReAct / 宸ュ叿璺緞

浠诲姟绫昏姹備細杩涘叆鏇村畬鏁寸殑 agent銆?
杩欐椂妯″瀷涓嶆槸鍙€滃洖绛斺€濓紝鑰屾槸鍙兘锛?
- 鍒ゆ柇瑕佽皟鐢ㄤ粈涔堝伐鍏枫€?- 璇诲彇鏂囦欢銆?- 鎿嶄綔绐楀彛銆?- 鍙戞秷鎭€?- 寤轰换鍔°€?- 鍐欒蹇嗐€?- 姹囨姤鎵ц缁撴灉銆?
鎵€浠ヤ换鍔＄被璇煶澶╃劧姣旈棽鑱婃參銆?
## 9. 浠?L3 鏂囧瓧鍒?TTS

鍓嶇涓嶆槸绛夊畬鏁村洖绛旂粨鏉熸墠寮€濮嬫湕璇汇€?
瀹冧細鎺ユ敹 L3 鐨?chunk锛?
```text
l3.chunk
```

鐒跺悗浜ょ粰锛?
```text
voiceOrchestrator.onL3Chunk
```

`voiceOrchestrator` 鍋氬嚑浠朵簨锛?
1. 鍚堝苟娴佸紡 delta銆?2. 鐢?`sentenceBuffer.ts` 鎸夋爣鐐规媶鍙ャ€?3. 鐢?`speakableText.ts` 娓呯悊涓嶉€傚悎鏈楄鐨勫唴瀹广€?4. 鍘婚噸锛岄伩鍏嶉噸澶嶈鍚屼竴鍙ャ€?5. 閫愬彞璋冪敤 JVS TTS銆?6. 鏀惧叆鎾斁闃熷垪銆?
### 鍒嗗彞瑙勫垯

纭柇鍙ワ細

```text
銆傦紒锛?!?
```

杞柇鍙ワ細

```text
锛?銆?```

浣嗚蒋鏂彞瑕佹眰褰撳墠鐗囨鑷冲皯鏈変竴瀹氶暱搴︼紝閬垮厤澶煭灏卞垏銆?
### TTS 娓呮礂

鏈楄鍓嶄細鍘绘帀锛?
- Markdown 浠ｇ爜鍧椼€?- 琛屽唴浠ｇ爜銆?- 閮ㄥ垎绗﹀彿銆?- emoji銆?- 澶儚杩囩▼璇存槑鐨勫彞瀛愩€?- 澶儚鍒楄〃姝ラ浣嗘病鏈夌粨鏋滄彁绀虹殑鍙ュ瓙銆?
鍘熷洜鏄闊抽櫔浼存€佷笉閫傚悎鎶婂畬鏁存棩蹇椼€佽〃鏍笺€佹帹鐞嗛摼鏉°€佷唬鐮佸潡蹇靛嚭鏉ャ€?
## 10. TTS 妯″瀷

褰撳墠 JVS TTS 鏄?Kokoro ONNX銆?
鏍稿績鏂囦欢锛?
```text
voice_server/services/tts_service.py
```

榛樿閰嶇疆锛?
```text
voice = zm_053
speed = 1.25
sample_rate = 24000
model = Kokoro-82M-v1.1-zh-ONNX
```

鍓嶇榛樿鍊硷細

```text
clients/desktop/src/voice/voiceDefaults.ts
```

### Kokoro 鍚堟垚娴佺▼

JVS `/v1/tts/synthesize` 鏀跺埌鏂囨湰鍚庯紝澶ф娴佺▼锛?
```text
鏂囨湰褰掍竴鍖?  -> 涓枃鍓嶇澶勭悊
  -> jieba 鍒嗚瘝
  -> pypinyin 鍙栨嫾闊冲拰澹拌皟
  -> misaki zh 杞?IPA
  -> phoneme 鏄犲皠鍒?tokenizer vocab
  -> 閫夋嫨 voice bin
  -> 鏍规嵁 token 闀垮害閫夋嫨 style vector
  -> ONNX 鎺ㄧ悊
  -> 淇壀棣栧熬闈欓煶
  -> 杈撳嚭 WAV
```

杩欓噷瑕佹敞鎰忥細TTS 涓嶆槸鈥滆皟鐢ㄤ竴涓嬪氨瀹屼簨鈥濄€侹okoro 涓枃閾捐矾闇€瑕佽嚜宸卞鐞嗭細

- 鏁板瓧鎬庝箞璇汇€?- 鑻辨枃鎬庝箞娣疯銆?- 涓枃鏍囩偣鎬庝箞褰卞搷鍋滈】銆?- 鎷奸煶澹拌皟鎬庝箞淇濈暀銆?- phoneme 閲屾ā鍨嬩笉璁よ瘑鐨勭鍙锋€庝箞鏄犲皠銆?- voice bin 鐨?style index 鎬庝箞閫夈€?- 棣栧熬闈欓煶鎬庝箞瑁併€?
鎵€浠ヤ箣鍓嶅嚭鐜扳€滄柟瑷€鎰熴€侀粡杩炪€佽嫳鏂囦笉璇汇€佸畬鎴愯涓嶆竻妤氣€濇椂锛屾湰璐ㄥ鍗婁笉鏄挱鏀鹃棶棰橈紝鑰屾槸涓枃鍓嶇銆乸honeme 鏄犲皠銆佹爣鐐瑰仠椤裤€乻tyle vector 閫夋嫨杩欎簺灞傚嚭浜嗗亸宸€?
### TTS 杩斿洖缁欏墠绔殑璇婃柇澶?
JVS TTS 浼氬湪 HTTP header 閲屽甫璇婃柇淇℃伅锛?
```text
X-Jachin-Duration-Ms
X-Jachin-Sample-Rate
X-Jachin-TTS-Synth-Ms
X-Jachin-TTS-Attempts
X-Jachin-TTS-Quality
X-Jachin-TTS-Kind
X-Jachin-TTS-Style-Index
X-Jachin-TTS-Style-Mode
X-Jachin-TTS-Raw-Duration-Ms
X-Jachin-TTS-Trim-Leading-Ms
X-Jachin-TTS-Trim-Trailing-Ms
```

鍓嶇浼氭妸杩欎簺鍐欏埌 `voice_chat.log`锛岀敤浜庡垽鏂埌搴曟槸锛?
- L3 鎱€?- TTS 鍚堟垚鎱€?- 鎾斁闃熷垪鎱€?- 闊抽鏈韩澶暱銆?- 棣栧熬闈欓煶澶暱銆?
## 11. 鎾斁鍜屾墦鏂?
鎾斁鐢憋細

```text
voicePlaybackController
```

璐熻矗銆?
瀹冩湁涓€涓?generation 鏈哄埗銆?
绠€鍗曡锛?
```text
姣忔鏂拌闊充細璇?/ 鎵撴柇 -> generation +1
鏃?generation 鐨?TTS 缁撴灉鍗充娇鏅氬埌锛屼篃涓嶈缁х画鎾斁
```

鎵撴柇鍏ュ彛锛?
```text
voiceOrchestrator.bargeIn()
```

瀹冧細锛?
- 鍋滄褰撳墠鎾斁銆?- 娓呯┖鎾斁闃熷垪銆?- bump generation銆?- 璋?JVS `/v1/session/cancel` 鍙栨秷瀵瑰簲 session 鐨?TTS銆?- UI 鍥炲埌 listening銆?
杩欏氨鏄敤鎴封€滄棫璇锋眰澶參锛屾垜鍙堝彂浜嗘柊璇煶鈥濇椂锛岀郴缁熷簲璇ュ仛鐨勪簨銆?
## 12. 浠诲姟娌℃墽琛屽畬鏃剁敤鎴风户缁璇?
杩欎釜鍦烘櫙鐢变袱涓眰鍏卞悓澶勭悊銆?
### 鍓嶇璺敱灞?
鍓嶇淇濆瓨 active voice tasks銆?
濡傛灉鏈変换鍔℃鍦ㄨ窇锛屾柊璇煶浼氬厛琚垽鏂槸涓嶆槸鎺у埗璇锋眰锛?
```text
鍙栨秷 / 鍋滄 -> ABORT
杩涘害 / 鍋氬埌鍝簡 -> STATUS
鏀规垚 / 鍐嶅姞 -> MODIFY
缁х画 -> RESUME
鍏朵粬鏂拌瘽棰?-> PARALLEL 鎴?direct_llm
```

杩欎竴姝ョ殑鐩爣鏄笉瑕佹妸鈥滃仠涓€涓嬧€濊褰撴垚鏅€氳亰澶┿€?
### L3 / task 灞?
濡傛灉璺敱缁撴灉鏄换鍔℃帶鍒讹紝浼氳繘鍏?L3 鐨勫悗鍙版帶鍒惰矾寰勩€?
濡傛灉鏄櫘閫氳亰澶╀絾鏈?active task锛屽墠绔彲浠ユ敞鍏ヨ交閲忎换鍔′笂涓嬫枃锛?
```text
inject_light_task_context = true
```

杩欐牱妯″瀷鍙互鐭ラ亾鈥滃悗鍙版湁涓换鍔♀€濓紝浣嗕笉浼氱紪閫犺繘搴︺€?
## 13. 鏃ュ織鎬庝箞鐪?
### voice_chat.log

璺緞閫氬父鏄細

```text
C:/Users/Samuel/.jachin/jachin_debug/voice_chat.log
```

杩欐槸鏈€閲嶈鐨勭鍒扮璇煶閾捐矾鏃ュ織銆?
鍏抽敭闃舵锛?
```text
turn.begin
stt.audio_ready
sv.owner_track_ptt / sv.owner_track_ptt_fast_bypass
stt.prepare
stt.wav_ready
stt.jvs_ready
stt.jvs_transcribe_request
stt.jvs_transcribe_ok
stt.recognized
l3.send_start
l3.route_decision
l3.ws_send_ok
l3.chunk
l3.answer
tts.orchestrator.start
tts.orchestrator.chunk
tts.orchestrator.request
tts.jvs_fetch_start
tts.jvs_fetch_response
tts.jvs_blob_ok
tts.orchestrator.ok
tts.playback_enqueue
tts.playback_native_start / tts.playback_web_start
tts.playback_native_done / tts.playback_web_ended
turn.end
```

濡傛灉鏂囧瓧寰堜箙鎵嶅嚭鏉ワ紝鐪嬶細

```text
l3.send_start -> l3.chunk / l3.answer
```

濡傛灉鏂囧瓧鍑烘潵浜嗕絾寰堜箙鎵嶈璇濓紝鐪嬶細

```text
tts.orchestrator.request -> tts.jvs_fetch_response -> tts.playback_start
```

濡傛灉 STT 鎱紝鐪嬶細

```text
stt.audio_ready -> stt.recognized
```

濡傛灉澹扮汗鎱紝鐪嬶細

```text
sv.owner_track_ptt latencyMs
```

### voice_companion.log

璺緞閫氬父鏄細

```text
C:/Users/Samuel/.jachin/jachin_debug/voice_companion.log
```

瀹冩洿鍋?UI / 闄即鎬佺姸鎬佹祦锛屾瘮濡?Orb銆丠UD銆佷細璇濄€乀TS 闃熷垪鐘舵€併€?
### terminal_turn 鏃ュ織

璺緞閫氬父鏄細

```text
C:/Users/Samuel/.jachin/jachin_debug/terminal_turn_*.log
```

瀹冩洿鍋?L3 鍐呴儴 agent 杩囩▼锛屾瘮濡?direct_llm_bypass銆丷eAct銆佸伐鍏疯皟鐢ㄣ€佸紓甯搞€?
## 14. 褰撳墠绯荤粺鏈€瀹规槗娣蜂贡鐨勫湴鏂?
### 14.1 鈥滃揩璺敱鈥濇湁涓ゅ眰

绗竴灞傚湪鍓嶇锛?
```text
voiceIntentRouter.ts
```

绗簩灞傚湪 L3锛?
```text
ws_server.py
agent_core.py
```

濡傛灉涓ゅ眰鐞嗚В涓嶄竴鑷达紝灏变細鍑虹幇锛?
- 鍓嶇璁や负鏄交闂瓟銆?- L3 褰撴垚 presence ack銆?- 鐢ㄦ埛闂€滀粖澶╁悆浠€涔堚€濓紝绯荤粺绛斺€滄垜鍦ㄢ€濄€?
鐜板湪宸茬粡閫氳繃 `voice_fast_lane_kind` 鍜?`voice_allow_template_reply` 鎶婅繖浠朵簨鎷夐綈锛?
```text
presence_template 鎵嶈兘妯℃澘绛斺€滄垜鍦ㄢ€?light_query 绂佹妯℃澘锛屽繀椤婚棶妯″瀷
```

### 14.2 鈥滆鍒欒竟鐣屸€濆拰鈥滃ぇ妯″瀷鑷敱搴︹€濊鍒嗘竻

鐜板湪鐨勮矾鐢卞亸瑙勫垯涓诲锛屼絾涓嶆槸瑙勫垯鍐欐鎵€鏈夊洖澶嶃€?
瑙勫垯璐熻矗锛?
- 鍒嗗眰銆?- 瀹夊叏杈圭晫銆?- 鏄惁鎵ц銆?- 鏄惁鍚庡彴銆?- 鏄惁璺宠繃閲嶉摼璺€?- 鏄惁鍏佽妯℃澘銆?
澶фā鍨嬭礋璐ｏ細

- 闂茶亰鎬庝箞璇淬€?- 杞婚棶绛旀€庝箞鍥炵瓟銆?- 杩介棶鎬庝箞鑷劧琛ㄨ揪銆?- 浠诲姟缁撴灉鎬庝箞缁勭粐璇█銆?
杩欐槸姣旇緝鍚堢悊鐨勬柟鍚戙€傞棶棰橀€氬父涓嶅湪鈥滄湁娌℃湁瑙勫垯鈥濓紝鑰屽湪瑙勫垯鏄惁鎶婃煇绫昏瘽璇垎鍒伴敊璇溅閬撱€?
### 14.3 TTS 鐨勪腑鏂囧墠绔緢鏁忔劅

Kokoro 涓嶆槸瀹屾暣涓枃浜у搧绾?TTS 灏佽锛岃€屾槸 ONNX 妯″瀷鍔犱竴鍫嗘湰鍦板墠绔€傞厤銆?
浠讳綍涓€灞傚嚭閿欓兘鍙兘褰卞搷鍚劅锛?
- 涓枃鏍囩偣琚鐞嗛敊锛屾柇鍙ヤ細鎬€?- phoneme OOV 琚涪锛屽瓧浼氬惈娣枫€?- 澹拌皟绗﹀彿涓㈠け锛屼細鏈夋柟瑷€鎰熴€?- style index 涓嶅悎閫傦紝璇皵浼氶銆?- 鑻辨枃娣疯娌″綊涓€鍖栵紝浼氳烦璇绘垨涔辫銆?- 棣栧熬闈欓煶瑁佸壀涓嶅悎閫傦紝浼氶粡杩炴垨鎶㈡媿銆?
### 14.4 鏃ц姹傛櫄鍒颁笌鏂拌姹傛姠鎾斁

绯荤粺鐢?generation 鍜?session cancel 瑙ｅ喅杩欎釜闂銆?
浣嗗鏋滄煇涓棫璇锋眰鍦?L3 鎴?TTS 鍐呴儴鍗″緢涔咃紝浠嶇劧鍙兘鍑虹幇鈥滄櫄鍒扮粨鏋溾€濄€傝繖鏃惰鐪嬫棩蹇楃‘璁わ細

- 鏃ц姹傛槸鍚﹁ cancel銆?- 鏃?TTS 鏄惁浠嶇劧杩涘叆鎾斁闃熷垪銆?- generation 鏄惁姝ｇ‘鎷︽埅鏃ч煶棰戙€?
## 15. 涓€鍙ヨ瘽鎬荤粨

鐜板湪璇煶闄即鎬佸彲浠ョ悊瑙ｆ垚鍥涘眰锛?
```text
鎰熺煡灞傦細褰曢煶銆佸０绾广€丼TT
璺敱灞傦細鍒ゆ柇闂茶亰銆佽交闂瓟銆佺煭浠诲姟銆侀暱浠诲姟銆佷换鍔℃帶鍒?鏅鸿兘灞傦細妯℃澘銆佸揩妯″瀷銆乨irect LLM銆佸畬鏁?Agent / 宸ュ叿 / 浠诲姟
琛ㄨ揪灞傦細娴佸紡鏂囧瓧銆佸垎鍙ャ€乀TS銆佹挱鏀俱€佹墦鏂?```

鏈€鐞嗘兂鐨勮繍琛屾柟寮忔槸锛?
- 鈥滀綘濂?/ 鍦ㄥ悧鈥濈鍥炶繛鎺ユ劅銆?- 鈥滀粖澶╁悆浠€涔堚€濊蛋杞婚棶绛旓紝涓嶈繘浠诲姟閾撅紝涔熶笉妯℃澘鏁疯銆?- 鈥滃府鎴戞墦寮€璁＄畻鍣ㄢ€濊蛋鐭换鍔°€?- 鈥滄妸鐩綍鐢熸垚鎶ュ憡鈥濊蛋鍚庡彴闀夸换鍔°€?- 浠诲姟鎵ц涓敤鎴疯鈥滃仠涓€涓?/ 杩涘害鎬庝箞鏍?/ 鏀规垚杩欐牱鈥濓紝璧颁换鍔℃帶鍒躲€?- 鐢ㄦ埛鎵撴柇鏃ц闊虫椂锛屾棫 TTS 鍜屾棫鎾斁闃熷垪琚彇娑堬紝鏂拌姹備紭鍏堛€?
濡傛灉涔嬪悗瑕佺户缁紭鍖栵紝寤鸿鎸夋棩蹇楁妸闂褰掑洜鍒板叿浣撳眰锛?
```text
STT 鎱㈡垨閿?-> 鐪?voice_server STT / hotword / owner-track
璺敱閿?-> 鐪?voice_dispatch_decision / route_evidence
鏂囧瓧鎱?-> 鐪?L3 fast lane / direct_llm / run_agent
鏂囧瓧瀵逛絾璇磋瘽鎱?-> 鐪?TTS synth / playback queue
璇村緱闅惧惉 -> 鐪?Kokoro frontend / phoneme mapping / pause / style
鏃ц瘽涔辨彃 -> 鐪?generation / cancel / playback queue
```

- 主路径按当前本机配置写：cloud STT `fun-asr-realtime`，cloud TTS `cosyvoice-v3-plus`。
- 本地 Sherpa 和 Kokoro 只作为 fallback 说明。
- 语音层只做 STT、声纹、热词、实体纠错和 TTS，不做意图路由或缺槽追问。
- L3 负责所有任务理解、追问、确认和执行。
- 任何“语音层判断缺槽并追问用户”“前端 voiceIntentRouter 决定语音任务路径”“当前已经使用 SenseVoice”“当前 STT 是 Kokoro”的说法都是错误的。
