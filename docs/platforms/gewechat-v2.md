# GeWe v2 个人微信适配器

`gewechat-v2` 使用 GeWe v2 的 HTTP API 和 Webhook 回调接入个人微信。它和仓库里旧的 `legacy/gewechat` 适配器是两套实现，旧适配器不会被这个配置复用。

## 配置步骤

1. 在 GeWe 后台「访问控制」中取得 Token，并确认微信账号已经登录。
2. 在 LangBot 新建 `GeWe v2（个人微信）` Bot，填写 API 地址、Token 和 AppId。
3. 建议填写 `Webhook 路径密钥`。GeWe v2 文档没有提供 Webhook 签名校验，路径密钥是额外的入口保护。
4. 启动 Bot 后，把表单中显示的 `LangBot Webhook 地址` 填入 GeWe 的回调地址。开启「自动设置 GeWe 回调地址」时，适配器会调用 v2 的 `login/setCallback` 完成这一步。

默认回调地址形如：

```text
https://your-langbot.example/bots/<bot-uuid>/gewe/<webhook-secret>
```

没有填写路径密钥时，地址末尾为 `/gewe`。LangBot 的 `webhook_prefix` 必须是 GeWe 能访问到的公网地址；内网地址只适合本地联调。

## 重要限制

- GeWe 文档说明同一 Token 下的登录微信会共用一个回调地址。因此建议一个 LangBot Bot 只绑定一个 GeWe Token 和一个 AppId，不要把同一 Token 配给多个 LangBot Bot。
- 回调会按 `appid + newMsgId` 去重，`isSelf=true` 的消息不会进入 Pipeline。Webhook 先返回成功，再在后台处理消息，以满足 GeWe 的 3 秒响应要求。
- 部分 GeWe v2 账号仍会在回调中返回旧式 `TypeName=AddMsg` / `Data` 信封。适配器会在验证 AppId、去重和进入 Pipeline 前将它规范化为 v2 内部格式，并从 `Data.MsgSource` 的 `<atuserlist>` 识别群聊 @ 对象。
- 图片、文件、语音和视频接收时会调用 GeWe v2 下载接口。发送媒体时，LangBot 组件需要提供公网可访问的 URL；本地路径或纯 Base64 不能直接交给 GeWe 的 `post*` 接口。
- 文本、图片、语音、视频、表情、文件、链接、小程序、AppMsg 和常见引用消息已做转换。位置、名片及其他未实现类型会保留为清晰的 `Unknown` 文本，不会因为类型未知而抛出回调异常。
- 语音发送的时长按照 GeWe 文档使用毫秒；视频发送接口要求视频链接、缩略图链接和秒数。LangBot 当前没有独立的 Video 消息组件，因此接收视频会以 `File(video.mp4)` 进入 Pipeline。

## 安全建议

Token 只保存于 Bot 的适配器配置，不要写进代码或提交到 Git。管理后台对普通查看权限会隐藏适配器密钥；资源管理权限才能看到带路径密钥的完整回调地址。
