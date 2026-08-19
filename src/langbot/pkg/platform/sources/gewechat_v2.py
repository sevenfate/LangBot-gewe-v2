from __future__ import annotations

import asyncio
import datetime
import json
import re
import typing
import urllib.parse
import xml.etree.ElementTree as ET

import aiohttp
import pydantic
import quart

import langbot_plugin.api.definition.abstract.platform.adapter as abstract_platform_adapter
import langbot_plugin.api.entities.builtin.platform.entities as platform_entities
import langbot_plugin.api.entities.builtin.platform.events as platform_events
import langbot_plugin.api.entities.builtin.platform.message as platform_message
from ...utils import httpclient


_MAX_INBOUND_BODY = 4 * 1024 * 1024
_MAX_INBOUND_TASKS = 256
_DEDUP_TTL_SECONDS = 6 * 60 * 60
_DEDUP_MAX_ENTRIES = 8192
_MEMBER_NAME_CACHE_TTL_SECONDS = 10 * 60
_MEMBER_NAME_CACHE_MAX_ENTRIES = 2048
_DEFAULT_API_BASE_URL = 'http://api.geweapi.com'
_STATUS_EVENT_TYPES = {
    'RECONNECT_SUCCESS',
    'RECONNECT_FAIL',
    'LONG_SUCCESS',
    'LONG_FAIL',
    'LOGIN_ERROR',
    'LOGOUT',
    'LOGIN_SUCCESS',
    'LONG_SERVE_START_SUCCESS',
    'LONG_SERVE_CLOSE',
}
_LEGACY_MESSAGE_TYPES = {
    1: 'TEXT',
    3: 'IMAGE',
    34: 'VOICE',
    42: 'CARD',
    43: 'VIDEO',
    47: 'EMOJI',
    48: 'LOCATION',
}
_LEGACY_APP_MESSAGE_TYPES = {
    '5': 'LINK',
    '6': 'FILE',
    '33': 'MINI_PROGRAM',
    '36': 'MINI_PROGRAM',
    '57': 'QUOTE',
}


class GeWeV2ApiError(RuntimeError):
    """Raised when GeWe v2 rejects an API request."""


class GeWeV2Client:
    """Small async client for the documented GeWe v2 HTTP API."""

    def __init__(self, base_url: str, token: str, timeout: float = 20.0):
        self.base_url = base_url.rstrip('/')
        self.token = token
        self.timeout = aiohttp.ClientTimeout(total=timeout)

    async def post(self, endpoint: str, payload: dict) -> dict:
        headers = {
            'Content-Type': 'application/json',
            'X-GEWE-TOKEN': self.token,
        }
        session = httpclient.get_session()
        async with session.post(
            f'{self.base_url}/gewe/v2/api/{endpoint.lstrip("/")}',
            json=payload,
            headers=headers,
            timeout=self.timeout,
        ) as response:
            body = await httpclient.read_json_limited(response)
            if response.status >= 400:
                raise GeWeV2ApiError(f'GeWe HTTP {response.status}: {body!r}')
            if not isinstance(body, dict) or body.get('ret') not in (None, 200):
                raise GeWeV2ApiError(f'GeWe API rejected request: {body!r}')
            return body

    async def set_callback(self, callback_url: str) -> dict:
        return await self.post('login/setCallback', {'token': self.token, 'callbackUrl': callback_url})

    async def post_text(self, app_id: str, to_wxid: str, content: str, ats: str = '') -> dict:
        return await self.post(
            'message/postText',
            {'appId': app_id, 'toWxid': to_wxid, 'content': content, 'ats': ats},
        )

    async def get_chatroom_member_detail(
        self,
        app_id: str,
        chatroom_id: str,
        member_wxids: list[str],
    ) -> dict:
        return await self.post(
            'group/getChatroomMemberDetail',
            {'appId': app_id, 'chatroomId': chatroom_id, 'memberWxids': member_wxids},
        )

    async def post_file(self, app_id: str, to_wxid: str, file_url: str, file_name: str) -> dict:
        return await self.post(
            'message/postFile',
            {'appId': app_id, 'toWxid': to_wxid, 'fileUrl': file_url, 'fileName': file_name},
        )

    async def post_image(self, app_id: str, to_wxid: str, image_url: str) -> dict:
        return await self.post('message/postImage', {'appId': app_id, 'toWxid': to_wxid, 'imgUrl': image_url})

    async def post_voice(self, app_id: str, to_wxid: str, voice_url: str, duration_ms: int) -> dict:
        return await self.post(
            'message/postVoice',
            {'appId': app_id, 'toWxid': to_wxid, 'voiceUrl': voice_url, 'voiceDuration': duration_ms},
        )

    async def post_video(self, app_id: str, to_wxid: str, video_url: str, thumb_url: str, duration: int) -> dict:
        return await self.post(
            'message/postVideo',
            {
                'appId': app_id,
                'toWxid': to_wxid,
                'videoUrl': video_url,
                'thumbUrl': thumb_url,
                'videoDuration': duration,
            },
        )

    async def post_link(
        self,
        app_id: str,
        to_wxid: str,
        title: str,
        desc: str,
        link_url: str,
        thumb_url: str,
    ) -> dict:
        return await self.post(
            'message/postLink',
            {
                'appId': app_id,
                'toWxid': to_wxid,
                'title': title,
                'desc': desc,
                'linkUrl': link_url,
                'thumbUrl': thumb_url,
            },
        )

    async def post_name_card(self, app_id: str, to_wxid: str, nickname: str, wxid: str) -> dict:
        return await self.post(
            'message/postNameCard',
            {'appId': app_id, 'toWxid': to_wxid, 'nickName': nickname, 'nameCardWxid': wxid},
        )

    async def post_emoji(self, app_id: str, to_wxid: str, emoji_md5: str, emoji_size: int) -> dict:
        return await self.post(
            'message/postEmoji',
            {'appId': app_id, 'toWxid': to_wxid, 'emojiMd5': emoji_md5, 'emojiSize': emoji_size},
        )

    async def post_appmsg(self, app_id: str, to_wxid: str, appmsg: str) -> dict:
        return await self.post('message/postAppMsg', {'appId': app_id, 'toWxid': to_wxid, 'appmsg': appmsg})

    async def post_mini_app(
        self,
        app_id: str,
        to_wxid: str,
        mini_app_id: str,
        display_name: str,
        page_path: str,
        cover_img_url: str,
        title: str,
        user_name: str,
    ) -> dict:
        return await self.post(
            'message/postMiniApp',
            {
                'appId': app_id,
                'toWxid': to_wxid,
                'miniAppId': mini_app_id,
                'displayName': display_name,
                'pagePath': page_path,
                'coverImgUrl': cover_img_url,
                'title': title,
                'userName': user_name,
            },
        )

    async def post_location(self, app_id: str, to_wxid: str, content: str) -> dict:
        return await self.post('message/postLocation', {'appId': app_id, 'toWxid': to_wxid, 'content': content})

    async def forward(self, kind: str, app_id: str, to_wxid: str, xml: str, **extra) -> dict:
        payload = {'appId': app_id, 'toWxid': to_wxid, 'xml': xml, **extra}
        return await self.post(f'message/forward{kind}', payload)

    async def download_image(self, app_id: str, xml: str, image_type: int = 2) -> str:
        result = await self.post('message/downloadImage', {'appId': app_id, 'xml': xml, 'type': image_type})
        return str((result.get('data') or {}).get('fileUrl') or '')

    async def download_voice(self, app_id: str, msg_id: int | str, xml: str) -> str:
        result = await self.post('message/downloadVoice', {'appId': app_id, 'msgId': msg_id, 'xml': xml})
        return str((result.get('data') or {}).get('fileUrl') or '')

    async def download_video(self, app_id: str, xml: str) -> str:
        result = await self.post('message/downloadVideo', {'appId': app_id, 'xml': xml})
        return str((result.get('data') or {}).get('fileUrl') or '')

    async def download_file(self, app_id: str, xml: str) -> str:
        result = await self.post('message/downloadFile', {'appId': app_id, 'xml': xml})
        return str((result.get('data') or {}).get('fileUrl') or '')

    async def download_emoji(self, app_id: str, emoji_md5: str) -> str:
        result = await self.post('message/downloadEmojiMd5', {'appId': app_id, 'emojiMd5': emoji_md5})
        return str((result.get('data') or {}).get('url') or '')


def _as_text(value: typing.Any) -> str:
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _timestamp(value: typing.Any) -> float:
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return datetime.datetime.now().timestamp()
    if timestamp > 100_000_000_000:
        timestamp /= 1000
    return timestamp


def _integer(value: typing.Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _xml_root(value: str) -> ET.Element | None:
    if not value or not value.lstrip().startswith('<'):
        return None
    try:
        return ET.fromstring(value)
    except ET.ParseError:
        return None


def _xml_text(root: ET.Element | None, path: str, default: str = '') -> str:
    if root is None:
        return default
    return (root.findtext(path) or default).strip()


def _legacy_string(value: typing.Any) -> str:
    if isinstance(value, dict):
        return _as_text(value.get('string'))
    return _as_text(value)


def _legacy_message_type(data: dict, content: str) -> str:
    msg_type = _integer(data.get('MsgType'), -1)
    if msg_type != 49:
        return _LEGACY_MESSAGE_TYPES.get(msg_type, f'LEGACY_{msg_type}')

    _, xml_content = _group_prefix(content)
    root = _xml_root(xml_content)
    app_type = _xml_text(root, './/appmsg/type')
    return _LEGACY_APP_MESSAGE_TYPES.get(app_type, 'APP_MSG')


def _legacy_mention_ids(msg_source: str) -> list[str]:
    root = _xml_root(msg_source)
    raw_ids = _xml_text(root, './/atuserlist')
    if not raw_ids:
        return []
    return [item.strip() for item in re.split(r'[,;]', raw_ids) if item.strip()]


def _normalize_callback_payload(payload: dict) -> dict:
    """Normalize the legacy AddMsg envelope sometimes returned by the v2 callback endpoint."""
    data = payload.get('Data')
    if str(payload.get('TypeName') or '').upper() != 'ADDMSG' or not isinstance(data, dict):
        return payload

    account_id = str(payload.get('Wxid') or '')
    from_user = _legacy_string(data.get('FromUserName'))
    content = _legacy_string(data.get('Content'))
    group_sender, _ = _group_prefix(content) if from_user.endswith('@chatroom') else (None, content)
    msg_source = _legacy_string(data.get('MsgSource'))

    return {
        'appid': str(payload.get('Appid') or ''),
        'wxid': account_id,
        'msgType': _legacy_message_type(data, content),
        'content': content,
        'createTime': data.get('CreateTime'),
        'fromUser': from_user,
        'toUser': _legacy_string(data.get('ToUserName')),
        'msgId': data.get('MsgId'),
        'newMsgId': data.get('NewMsgId'),
        'isSelf': bool(account_id and (from_user == account_id or group_sender == account_id)),
        'atUserList': _legacy_mention_ids(msg_source),
        'pushContent': _legacy_string(data.get('PushContent')),
        '_callbackFormat': 'legacy-addmsg',
    }


def _group_prefix(content: str) -> tuple[str | None, str]:
    match = re.match(r'^([A-Za-z0-9_@.\-]{3,80}):\s*(?:\n)?(.*)$', content, re.S)
    if not match:
        return None, content
    return match.group(1), match.group(2)


class GeWeV2MessageConverter(abstract_platform_adapter.AbstractMessageConverter):
    """Convert GeWe v2 callbacks and LangBot message chains."""

    def __init__(self, config: dict, client: GeWeV2Client):
        self.config = config
        self.client = client

    @staticmethod
    async def _safe_download(operation) -> str:
        try:
            return str(await operation)
        except Exception:
            return ''

    def _group_info(self, payload: dict, content: str) -> tuple[str, str, str]:
        from_user = str(payload.get('fromUser') or '')
        to_user = str(payload.get('toUser') or '')
        group_id = from_user if from_user.endswith('@chatroom') else to_user if to_user.endswith('@chatroom') else ''
        sender_id = from_user
        clean_content = content
        if group_id and sender_id == group_id:
            prefixed_sender, clean_content = _group_prefix(content)
            sender_id = prefixed_sender or str(payload.get('sender') or payload.get('senderWxid') or group_id)
        return group_id, sender_id, clean_content

    @staticmethod
    def _mention_ids(payload: dict) -> list[str]:
        ids: list[str] = []
        for key, value in payload.items():
            normalized = str(key).lower()
            if 'at' not in normalized or not ('user' in normalized or 'wxid' in normalized or 'mention' in normalized):
                continue
            values = value if isinstance(value, list) else str(value).replace(';', ',').split(',')
            for item in values:
                item = str(item).strip()
                if item and item not in ids:
                    ids.append(item)
        return ids

    def _message_mentions(self, payload: dict, content: str, bot_id: str) -> list[platform_message.MessageComponent]:
        mention_ids = self._mention_ids(payload)
        nickname = str(self.config.get('bot_nickname') or '')
        bot_mentioned = bool(bot_id and bot_id in mention_ids)
        bot_mentioned = bot_mentioned or ('在群聊中@了你' in content)
        bot_mentioned = bot_mentioned or bool(nickname and f'@{nickname}' in content)
        result: list[platform_message.MessageComponent] = []
        if '@所有人' in content or '@all' in content.lower():
            result.append(platform_message.AtAll())
        for target in mention_ids:
            result.append(platform_message.At(target=target))
        if bot_mentioned and bot_id and bot_id not in mention_ids:
            result.append(platform_message.At(target=bot_id, display=nickname or bot_id))
        return result

    async def target2yiri(self, payload: dict, bot_account_id: str = '') -> platform_events.MessageEvent | None:
        if not isinstance(payload, dict) or payload.get('isSelf') is True:
            return None
        msg_type = str(payload.get('msgType') or '').upper()
        content = _as_text(payload.get('content'))
        group_id, sender_id, clean_content = self._group_info(payload, content)
        components: list[platform_message.MessageComponent] = [
            platform_message.Source(
                id=str(payload.get('newMsgId') or payload.get('msgId') or ''),
                time=datetime.datetime.fromtimestamp(_timestamp(payload.get('createTime'))),
            )
        ]
        if group_id:
            components.extend(self._message_mentions(payload, clean_content, bot_account_id))

        if msg_type == 'TEXT':
            text = clean_content
            if group_id:
                text = re.sub(r'@[^\s]+', '', text).strip()
            components.append(platform_message.Plain(text=text))
        elif msg_type == 'IMAGE':
            url = await self._safe_download(self.client.download_image(self.config['app_id'], clean_content))
            components.append(
                platform_message.Image(url=url) if url else platform_message.Unknown(text='[GeWe 图片下载失败]')
            )
            components.append(platform_message.WeChatForwardImage(xml_data=clean_content))
        elif msg_type == 'VOICE':
            url = await self._safe_download(
                self.client.download_voice(self.config['app_id'], payload.get('msgId', 0), clean_content)
            )
            root = _xml_root(clean_content)
            duration_ms = 0
            if root is not None:
                voice = root.find('.//voicemsg')
                duration_ms = _integer(voice.get('voicelength', '0') if voice is not None else '0')
            components.append(
                platform_message.Voice(url=url, length=max(0, round(duration_ms / 1000)))
                if url
                else platform_message.Unknown(text='[GeWe 语音下载失败]')
            )
        elif msg_type == 'VIDEO':
            url = await self._safe_download(self.client.download_video(self.config['app_id'], clean_content))
            components.append(
                platform_message.File(name='video.mp4', url=url)
                if url
                else platform_message.Unknown(text='[GeWe 视频下载失败]')
            )
        elif msg_type == 'EMOJI':
            root = _xml_root(clean_content)
            emoji = root.find('.//emoji') if root is not None else None
            md5 = str(payload.get('emojiMd5') or (emoji.get('md5', '') if emoji is not None else ''))
            size = _integer(payload.get('emojiSize') or (emoji.get('size', '0') if emoji is not None else 0))
            if md5:
                components.append(platform_message.WeChatEmoji(emoji_md5=md5, emoji_size=size))
            else:
                components.append(platform_message.Unknown(text='[GeWe 表情缺少 md5]'))
        elif msg_type == 'FILE':
            root = _xml_root(clean_content)
            appmsg = root.find('.//appmsg') if root is not None else None
            file_url = await self._safe_download(self.client.download_file(self.config['app_id'], clean_content))
            name = _xml_text(root, './/appmsg/title', 'file')
            if appmsg is not None:
                ext = _xml_text(appmsg, './/appattach/fileext', '')
                if ext and '.' not in name:
                    name = f'{name}.{ext}'
            components.append(
                platform_message.File(name=name, url=file_url)
                if file_url
                else platform_message.WeChatForwardFile(xml_data=clean_content)
            )
        elif msg_type == 'LINK':
            root = _xml_root(clean_content)
            components.append(
                platform_message.WeChatLink(
                    link_title=_xml_text(root, './/appmsg/title'),
                    link_desc=_xml_text(root, './/appmsg/des'),
                    link_url=_xml_text(root, './/appmsg/url'),
                    link_thumb_url=_xml_text(root, './/appmsg/thumburl'),
                )
            )
            components.append(platform_message.WeChatForwardLink(xml_data=clean_content))
        elif msg_type == 'MINI_PROGRAM':
            root = _xml_root(clean_content)
            components.append(
                platform_message.WeChatForwardMiniPrograms(
                    xml_data=clean_content,
                    image_url=_xml_text(root, './/appmsg/thumburl') or None,
                )
            )
        elif msg_type in {'APP_MSG', 'QUOTE'}:
            components.append(platform_message.WeChatAppMsg(app_msg=clean_content))
        elif msg_type == 'LOCATION':
            components.append(platform_message.Unknown(text=f'[GeWe 位置消息] {clean_content}'))
        elif msg_type == 'CARD':
            components.append(platform_message.Unknown(text=f'[GeWe 名片消息] {clean_content}'))
        else:
            components.append(
                platform_message.Unknown(
                    text=f'[GeWe v2 未支持消息类型: {msg_type or "UNKNOWN"}] {clean_content[:500]}'
                )
            )

        if group_id:
            group = platform_entities.Group(
                id=group_id,
                name=group_id,
                permission=platform_entities.Permission.Member,
            )
            event = platform_events.GroupMessage(
                sender=platform_entities.GroupMember(
                    id=sender_id,
                    member_name=sender_id,
                    permission=platform_entities.Permission.Member,
                    group=group,
                ),
                message_chain=platform_message.MessageChain(components),
                time=_timestamp(payload.get('createTime')),
                source_platform_object={**payload, '_target_id': group_id, '_sender_id': sender_id},
            )
        else:
            event = platform_events.FriendMessage(
                sender=platform_entities.Friend(
                    id=sender_id,
                    nickname=str(payload.get('nickname') or sender_id),
                    remark=str(payload.get('remark') or ''),
                ),
                message_chain=platform_message.MessageChain(components),
                time=_timestamp(payload.get('createTime')),
                source_platform_object={**payload, '_target_id': sender_id, '_sender_id': sender_id},
            )
        return event

    @staticmethod
    async def yiri2target(message_chain: platform_message.MessageChain) -> list[dict]:
        """Convert a LangBot chain into neutral component dictionaries."""
        return [component.model_dump() for component in message_chain]


class GeWeV2Adapter(abstract_platform_adapter.AbstractMessagePlatformAdapter):
    """LangBot adapter for the GeWe v2 personal WeChat API."""

    name: str = 'gewechat-v2'
    listeners: dict = pydantic.Field(default_factory=dict, exclude=True)
    _client: GeWeV2Client = pydantic.PrivateAttr()
    _converter: GeWeV2MessageConverter = pydantic.PrivateAttr()
    _bot_uuid: str = pydantic.PrivateAttr(default='')
    _inbound_tasks: set[asyncio.Task] = pydantic.PrivateAttr(default_factory=set)
    _dedup: dict[str, float] = pydantic.PrivateAttr(default_factory=dict)
    _member_name_cache: dict[tuple[str, str], tuple[str, float]] = pydantic.PrivateAttr(default_factory=dict)

    def __init__(self, config: dict, logger):
        config = dict(config or {})
        missing = [key for key in ('base_url', 'token', 'app_id') if not str(config.get(key) or '').strip()]
        if missing:
            raise ValueError(f'GeWe v2 缺少配置项: {", ".join(missing)}')
        client = GeWeV2Client(
            base_url=str(config.get('base_url') or _DEFAULT_API_BASE_URL),
            token=str(config['token']),
            timeout=float(config.get('timeout', 20) or 20),
        )
        super().__init__(config=config, logger=logger, bot_account_id=str(config.get('wxid') or ''), listeners={})
        self._client = client
        self._converter = GeWeV2MessageConverter(config, client)

    def set_bot_uuid(self, bot_uuid: str) -> None:
        self._bot_uuid = str(bot_uuid)

    def _webhook_path(self) -> str:
        secret = str(self.config.get('webhook_secret') or '').strip()
        return 'gewe' if not secret else f'gewe/{urllib.parse.quote(secret, safe="")}'

    def callback_url(self) -> str:
        configured = str(self.config.get('callback_url') or '').strip()
        if configured:
            return configured
        prefix = 'http://127.0.0.1:5300'
        ap = getattr(self.logger, 'ap', None)
        if ap is not None:
            prefix = str(
                getattr(getattr(ap, 'instance_config', None), 'data', {}).get('api', {}).get('webhook_prefix', prefix)
            )
        return f'{prefix.rstrip("/")}/bots/{self._bot_uuid}/{self._webhook_path()}'

    def get_launcher_id(self, event: platform_events.MessageEvent) -> str:
        if isinstance(event, platform_events.GroupMessage):
            return str(event.sender.group.id)
        return str(event.sender.id)

    def register_listener(self, event_type: typing.Type[platform_events.Event], callback):
        self.listeners[event_type] = callback

    def unregister_listener(self, event_type: typing.Type[platform_events.Event], callback):
        if self.listeners.get(event_type) is callback:
            self.listeners.pop(event_type, None)

    def _reserve_dedup(self, key: str) -> bool:
        now = asyncio.get_running_loop().time()
        expired = [item for item, timestamp in self._dedup.items() if now - timestamp > _DEDUP_TTL_SECONDS]
        for item in expired[:256]:
            self._dedup.pop(item, None)
        if key in self._dedup:
            return False
        if len(self._dedup) >= _DEDUP_MAX_ENTRIES:
            oldest = min(self._dedup, key=self._dedup.get)
            self._dedup.pop(oldest, None)
        self._dedup[key] = now
        return True

    def _start_task(self, coro: typing.Coroutine) -> bool:
        self._inbound_tasks = {task for task in self._inbound_tasks if not task.done()}
        if len(self._inbound_tasks) >= _MAX_INBOUND_TASKS:
            coro.close()
            return False
        task = asyncio.create_task(coro)
        self._inbound_tasks.add(task)

        def done_callback(done_task: asyncio.Task) -> None:
            self._inbound_tasks.discard(done_task)
            if not done_task.cancelled():
                done_task.exception()

        task.add_done_callback(done_callback)
        return True

    async def _dispatch_payload(self, payload: dict) -> None:
        try:
            event = await self._converter.target2yiri(payload, self.bot_account_id)
            if event is None:
                return
            callback = self.listeners.get(type(event))
            if callback is not None:
                await callback(event, self)
        except Exception as exc:
            logger_error = getattr(self.logger, 'error', None)
            if callable(logger_error):
                await logger_error(f'GeWe v2 callback processing failed: {exc}')

    async def handle_unified_webhook(self, bot_uuid: str, path: str, request):
        if str(bot_uuid) != self._bot_uuid:
            return quart.jsonify({'ret': 404, 'msg': 'bot not found'}), 404
        if urllib.parse.unquote(path.strip('/')) != urllib.parse.unquote(self._webhook_path()):
            return quart.jsonify({'ret': 404, 'msg': 'invalid webhook path'}), 404
        body = await request.get_data()
        if len(body) > _MAX_INBOUND_BODY:
            return quart.jsonify({'ret': 413, 'msg': 'request too large'}), 413
        try:
            payload = await asyncio.to_thread(json.loads, body)
        except (json.JSONDecodeError, TypeError, ValueError):
            return quart.jsonify({'ret': 400, 'msg': 'invalid JSON'}), 400
        if not isinstance(payload, dict):
            return quart.jsonify({'ret': 400, 'msg': 'JSON object required'}), 400
        payload = _normalize_callback_payload(payload)
        app_id = str(payload.get('appid') or '')
        configured_app_id = str(self.config.get('app_id') or '')
        if app_id and app_id != configured_app_id:
            return quart.jsonify({'ret': 200, 'msg': 'ignored different appid'}), 200
        if payload.get('wxid'):
            self.bot_account_id = str(payload['wxid'])
            self.config['wxid'] = self.bot_account_id
        if payload.get('isSelf') is True:
            return quart.jsonify({'ret': 200, 'msg': 'ignored self message'}), 200
        msg_type = str(payload.get('msgType') or '').upper()
        if not msg_type or msg_type in _STATUS_EVENT_TYPES:
            return quart.jsonify({'ret': 200, 'msg': 'accepted'}), 200
        app_id = app_id or configured_app_id
        new_msg_id = str(payload.get('newMsgId') or payload.get('msgId') or '')
        if not new_msg_id:
            return quart.jsonify({'ret': 400, 'msg': 'newMsgId is required'}), 400
        if not self._reserve_dedup(f'{app_id}:{new_msg_id}'):
            return quart.jsonify({'ret': 200, 'msg': 'duplicate'}), 200
        dedup_key = f'{app_id}:{new_msg_id}'
        if not self._start_task(self._dispatch_payload(payload)):
            self._dedup.pop(dedup_key, None)
            return quart.jsonify({'ret': 429, 'msg': 'too many messages'}), 429
        return quart.jsonify({'ret': 200, 'msg': '操作成功'}), 200

    @staticmethod
    def _require_url(component: typing.Any, label: str) -> str:
        url = str(getattr(component, 'url', '') or '')
        if not url:
            raise ValueError(f'GeWe v2 {label} 需要公网可访问的 URL')
        return url

    async def _resolve_at_labels(self, chatroom_id: str, wxids: list[str], labels: list[str]) -> list[str]:
        if not chatroom_id.endswith('@chatroom'):
            return [label or wxid for wxid, label in zip(wxids, labels)]

        now = asyncio.get_running_loop().time()
        resolved = list(labels)
        missing: list[str] = []
        for index, (wxid, label) in enumerate(zip(wxids, labels)):
            if label or wxid == 'notify@all':
                continue
            cached = self._member_name_cache.get((chatroom_id, wxid))
            if cached and now - cached[1] <= _MEMBER_NAME_CACHE_TTL_SECONDS:
                resolved[index] = cached[0]
            elif wxid not in missing:
                missing.append(wxid)

        if missing:
            try:
                response = await self._client.get_chatroom_member_detail(
                    self.config['app_id'],
                    chatroom_id,
                    missing,
                )
                members = response.get('data') or []
                if isinstance(members, list):
                    for member in members:
                        if not isinstance(member, dict):
                            continue
                        wxid = str(member.get('userName') or '')
                        nickname = str(member.get('nickName') or '').strip()
                        if wxid and nickname:
                            self._member_name_cache[(chatroom_id, wxid)] = (nickname, now)
                if len(self._member_name_cache) > _MEMBER_NAME_CACHE_MAX_ENTRIES:
                    oldest = sorted(self._member_name_cache, key=lambda key: self._member_name_cache[key][1])
                    for key in oldest[: len(self._member_name_cache) - _MEMBER_NAME_CACHE_MAX_ENTRIES]:
                        self._member_name_cache.pop(key, None)
            except Exception as exc:
                await self.logger.warning(f'GeWe v2 获取群成员昵称失败: {exc}')

        for index, (wxid, label) in enumerate(zip(wxids, resolved)):
            if label:
                continue
            cached = self._member_name_cache.get((chatroom_id, wxid))
            resolved[index] = cached[0] if cached else wxid
        return resolved

    async def _send_chain(self, target_id: str, message: platform_message.MessageChain) -> None:
        pending_at: list[str] = []
        pending_labels: list[str] = []

        async def flush_at(text: str) -> None:
            nonlocal pending_at, pending_labels
            if not text and not pending_at:
                return
            labels = await self._resolve_at_labels(target_id, pending_at, pending_labels)
            prefix = ''.join(f'@{label} ' for label in labels)
            await self._client.post_text(
                self.config['app_id'],
                target_id,
                f'{prefix}{text}'.strip(),
                ','.join(pending_at),
            )
            pending_at = []
            pending_labels = []

        for component in message:
            if isinstance(component, platform_message.Source):
                continue
            if isinstance(component, platform_message.AtAll):
                pending_at.append('notify@all')
                pending_labels.append('所有人')
                continue
            if isinstance(component, platform_message.At):
                pending_at.append(str(component.target))
                pending_labels.append(str(component.display or ''))
                continue
            if isinstance(component, platform_message.Plain):
                await flush_at(component.text)
            elif isinstance(component, platform_message.Image):
                await flush_at('')
                await self._client.post_image(self.config['app_id'], target_id, self._require_url(component, '图片'))
            elif isinstance(component, platform_message.File):
                await flush_at('')
                await self._client.post_file(
                    self.config['app_id'], target_id, self._require_url(component, '文件'), component.name or 'file'
                )
            elif isinstance(component, platform_message.Voice):
                await flush_at('')
                await self._client.post_voice(
                    self.config['app_id'],
                    target_id,
                    self._require_url(component, '语音'),
                    int(component.length or 0) * 1000,
                )
            elif isinstance(component, platform_message.WeChatLink):
                await flush_at('')
                await self._client.post_link(
                    self.config['app_id'],
                    target_id,
                    component.link_title,
                    component.link_desc,
                    component.link_url,
                    component.link_thumb_url,
                )
            elif isinstance(component, platform_message.WeChatEmoji):
                await flush_at('')
                await self._client.post_emoji(
                    self.config['app_id'], target_id, component.emoji_md5, component.emoji_size
                )
            elif isinstance(component, platform_message.WeChatMiniPrograms):
                await flush_at('')
                await self._client.post_mini_app(
                    self.config['app_id'],
                    target_id,
                    component.mini_app_id,
                    component.display_name or '',
                    component.page_path or '',
                    component.image_url or '',
                    component.title or '',
                    component.user_name,
                )
            elif isinstance(component, platform_message.WeChatAppMsg):
                await flush_at('')
                await self._client.post_appmsg(self.config['app_id'], target_id, component.app_msg)
            elif isinstance(component, platform_message.WeChatForwardQuote):
                await flush_at('')
                await self._client.post_appmsg(self.config['app_id'], target_id, component.app_msg)
            elif isinstance(component, platform_message.WeChatForwardImage):
                await flush_at('')
                await self._client.forward('Image', self.config['app_id'], target_id, component.xml_data)
            elif isinstance(component, platform_message.WeChatForwardFile):
                await flush_at('')
                await self._client.forward('File', self.config['app_id'], target_id, component.xml_data)
            elif isinstance(component, platform_message.WeChatForwardLink):
                await flush_at('')
                await self._client.forward('Url', self.config['app_id'], target_id, component.xml_data)
            elif isinstance(component, platform_message.WeChatForwardMiniPrograms):
                await flush_at('')
                await self._client.forward(
                    'MiniApp',
                    self.config['app_id'],
                    target_id,
                    component.xml_data,
                    coverImgUrl=component.image_url or '',
                )
            elif isinstance(component, platform_message.Unknown):
                await flush_at(component.text)
            else:
                await flush_at(str(component))
        await flush_at('')

    async def send_message(self, target_type: str, target_id: str, message: platform_message.MessageChain):
        await self._send_chain(str(target_id), message)

    async def reply_message(
        self,
        message_source: platform_events.MessageEvent,
        message: platform_message.MessageChain,
        quote_origin: bool = False,
    ):
        target_id = ''
        if isinstance(message_source.source_platform_object, dict):
            target_id = str(message_source.source_platform_object.get('_target_id') or '')
        if not target_id:
            target_id = str(
                message_source.sender.group.id
                if isinstance(message_source, platform_events.GroupMessage)
                else message_source.sender.id
            )
        await self._send_chain(target_id, message)

    async def is_muted(self, group_id: int) -> bool:
        return False

    async def is_stream_output_supported(self) -> bool:
        return False

    async def run_async(self):
        if self.config.get('auto_set_callback', True):
            await self._client.set_callback(self.callback_url())
        while True:
            await asyncio.sleep(3600)

    async def kill(self) -> bool:
        tasks = list(self._inbound_tasks)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._inbound_tasks.clear()
        self._dedup.clear()
        return True
