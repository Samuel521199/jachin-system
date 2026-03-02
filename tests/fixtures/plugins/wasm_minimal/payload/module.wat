;; 最小 WASM 插件：导出 memory 和 execute
;; execute(ptr, len) -> 返回 0 表示无输出
(module
  (memory (export "memory") 1 1)
  (func (export "execute") (param $ptr i32) (param $len i32) (result i32)
    (i32.const 0)
  )
)
