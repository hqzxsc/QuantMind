# 券商 OpenAPI Skills

富途官方提供的 Claude Code Skills（来源：https://openapi.futunn.com/skills/opend-skills.zip）：

- `install-futu-opend` — 富途 OpenD 安装助手（下载/安装/启动 OpenD，升级 futu-api SDK）
- `futuapi` — 富途 OpenAPI 市场数据与交易助手（行情/K线/下单/持仓/资金）

## 安装到 Claude Code

```bash
cp -r skills/install-futu-opend skills/futuapi ~/.claude/skills/
```

之后在 Claude Code 里即可使用 `/install-futu-opend` 与富途 API 助手。

## 关联网关容器

- 富途 OpenD 网关：`docker compose up -d futu-opend`（官方原包，API 端口 11111）
- IB Gateway：`docker compose up -d ib-gateway`（.env 配置 IB_ACCOUNT/IB_PASSWORD，端口 4001=实盘 / 4002=模拟）
