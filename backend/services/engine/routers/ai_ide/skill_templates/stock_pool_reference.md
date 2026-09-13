用途：用户提到股票池 / 限定选股范围时注入，声明各链路的池引用写法。

引用语法（后端 PoolResolver 唯一入口，大小写不敏感）：
- pool:csi300 / pool:csi500 / pool:csi1000 / pool:gem（推荐写法）
- 裸 code：csi300、all_a（先查库再回内置）
- list:SH600036,SZ000001（临时代码表）
- file:/绝对路径/x.txt（本地文件）
- all（显式不过滤 = 全市场）

各链路一行（用户在代码/参数里二选一写一行即可）：
1) Qlib 回测：请求带 pool_id="pool:csi300"（优先于 universe 解析物化）
2) 模型训练：第一步选池，pool:<code> 经 pool_id 透传（空池拒绝提交）
3) 单日推理：pool_id 透传，只裁信号，不进模型 pred.parquet
4) Strategy Lab（SDK，必须是 setup/on_bar/on_universe 风格）：
```python
def setup(ctx):
    ctx.universe = "csi300"
    ctx.stock_pool = "pool:csi1000"   # ← 多这一行：universe ∩ 股票池
```
5) 模拟盘回放 signals 模式：strategy_params 加 "pool_id": "pool:csi300"
6) 模拟盘回放 code 模式：同一套 SDK 代码，ctx.stock_pool 照写；
   会话 strategy_params 的 pool_id 仅作缺省（代码里写了以代码为准）
7) 实盘手动/托管：策略 live_trade_config 加 "pool_id" 键；
   模拟活盘：run_cycle(pool_id="pool:csi1000")

强制约束：
1) 空池 / 零命中一律显式失败，绝不静默退化为全市场（训练/推理/回放同约）。
2) 禁止编造池 code：可用内置池只有 csi300/csi500/csi1000/csi800/gem/
   sse50/star/hs300_ext/all_a（以 GET /api/v1/stock-pools/options 为准）；
   用户自定义池必须先在管理后台数据管理全局股票池维护。
3) 代码口径禁跨层混用：Qlib/行情层用后缀式（600036.SH），
   PG/Redis/前端/API 用前缀式（SH600036），层边界经 StockCodeUtil 转换。
4) SDK 的 stock_pool 只做格式校验（pool:/list:/file:/裸 code/all），
   不存在/无权的池在 runner 解析时报错，不要在生成代码里 try 吞掉。
5) 回测/模拟选池后，如用户问"为什么没成交"，先查池是否过小或零交集，
   不要改 topk 或放宽条件掩盖问题。

禁止：
- 输出 pool_files、user_pool:、cos:// 等旧引用写法
- 把"全市场"实现为超大 list（用 all 或不传池）
- 在策略代码里手写 >50 只的硬编码成分表（用池引用代替）
