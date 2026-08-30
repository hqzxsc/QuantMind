# features

用途：按业务划分的功能模块。

## 说明
- 归属路径：electron\src\features
- 修改本目录代码后请同步更新本 README
- `stock-terminal/pages/StockTerminalPage.tsx`：未选中股票的引导空态下方只保留一行温馨提示（`Database` 图标 + `text-[11px] text-slate-400`），提醒用户先下载完整行情数据包并保持每日更新；不引入跳转按钮、权限判断与额外卡片容器。
- `user-center/services/userCenterService.ts` 的 `normalizeAvatarUrl` 新增同源静态资源分支：以 `/` 开头且非 `//`、非 `/api/` 的头像路径（默认头像 `/logo.png`，来自 `electron/public`）不再拼接对象存储域名；桌面端打包后页面为 `file://` 协议时转成相对 `index.html` 的路径，保证 `public` 产物可加载。
