from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quart import Quart

import langbot_plugin.api.entities.builtin.platform.events as platform_events
import langbot_plugin.api.entities.builtin.platform.message as platform_message
from langbot.pkg.platform.sources import gewechat_v2


class FakeDownloadClient:
    async def download_image(self, *_args):
        return 'https://media.example/image.png'

    async def download_voice(self, *_args):
        return 'https://media.example/voice.silk'

    async def download_video(self, *_args):
        return 'https://media.example/video.mp4'

    async def download_file(self, *_args):
        return 'https://media.example/report.pdf'


@pytest.mark.asyncio
async def test_v2_text_and_group_mentions_are_converted():
    converter = gewechat_v2.GeWeV2MessageConverter(
        {'app_id': 'app', 'bot_nickname': '阿飞'},
        FakeDownloadClient(),
    )

    friend = await converter.target2yiri(
        {
            'appid': 'app',
            'wxid': 'wxid_bot',
            'msgType': 'TEXT',
            'content': '你好',
            'createTime': 1700000000,
            'fromUser': 'wxid_user',
            'toUser': 'wxid_bot',
            'newMsgId': 11,
            'isSelf': False,
        },
        'wxid_bot',
    )
    assert isinstance(friend, platform_events.FriendMessage)
    assert friend.sender.id == 'wxid_user'
    assert friend.message_chain.message_id == '11'
    assert friend.message_chain.get_first(platform_message.Plain).text == '你好'

    group = await converter.target2yiri(
        {
            'appid': 'app',
            'wxid': 'wxid_bot',
            'msgType': 'TEXT',
            'content': 'wxid_user:\n@阿飞 你好',
            'createTime': 1700000000,
            'fromUser': 'room@chatroom',
            'toUser': 'wxid_bot',
            'atUserList': 'wxid_bot',
            'newMsgId': 12,
            'isSelf': False,
        },
        'wxid_bot',
    )
    assert isinstance(group, platform_events.GroupMessage)
    assert group.sender.id == 'wxid_user'
    assert group.sender.group.id == 'room@chatroom'
    assert any(isinstance(item, platform_message.At) and item.target == 'wxid_bot' for item in group.message_chain)
    assert group.message_chain.get_first(platform_message.Plain).text == '你好'


@pytest.mark.asyncio
async def test_v2_media_download_and_unknown_type_are_safe():
    converter = gewechat_v2.GeWeV2MessageConverter({'app_id': 'app'}, FakeDownloadClient())
    image = await converter.target2yiri(
        {
            'appid': 'app',
            'msgType': 'IMAGE',
            'content': '<msg><img /></msg>',
            'fromUser': 'wxid_user',
            'toUser': 'wxid_bot',
            'newMsgId': 13,
            'isSelf': False,
        }
    )
    assert image.message_chain.get_first(platform_message.Image).url == 'https://media.example/image.png'
    assert image.message_chain.get_first(platform_message.WeChatForwardImage).xml_data == '<msg><img /></msg>'

    unknown = await converter.target2yiri(
        {
            'appid': 'app',
            'msgType': 'TRANSFER',
            'content': '<msg>transfer</msg>',
            'fromUser': 'wxid_user',
            'toUser': 'wxid_bot',
            'newMsgId': 14,
            'isSelf': False,
        }
    )
    assert '[GeWe v2 未支持消息类型: TRANSFER]' in unknown.message_chain.get_first(platform_message.Unknown).text

    assert await converter.target2yiri({'msgType': 'TEXT', 'isSelf': True}) is None


@pytest.mark.asyncio
async def test_api_client_uses_v2_path_and_token_header(monkeypatch):
    calls = []

    class Response:
        status = 200

    class Context:
        async def __aenter__(self):
            return Response()

        async def __aexit__(self, *_args):
            return False

    class Session:
        def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return Context()

    monkeypatch.setattr(gewechat_v2.httpclient, 'get_session', lambda: Session())
    monkeypatch.setattr(gewechat_v2.httpclient, 'read_json_limited', AsyncMock(return_value={'ret': 200}))

    client = gewechat_v2.GeWeV2Client('http://api.example', 'secret-token')
    await client.post_text('app', 'wxid_user', 'hello')

    assert calls[0][0] == 'http://api.example/gewe/v2/api/message/postText'
    assert calls[0][1]['headers']['X-GEWE-TOKEN'] == 'secret-token'
    assert calls[0][1]['json']['appId'] == 'app'
    assert calls[0][1]['json']['toWxid'] == 'wxid_user'


@pytest.mark.asyncio
async def test_unified_webhook_returns_quickly_and_deduplicates():
    converter = SimpleNamespace(target2yiri=AsyncMock(return_value=None))
    adapter = gewechat_v2.GeWeV2Adapter.model_construct(
        config={'app_id': 'app', 'webhook_secret': 'path-secret'},
        logger=SimpleNamespace(),
        bot_account_id='wxid_bot',
        listeners={},
        _bot_uuid='bot-uuid',
        _converter=converter,
        _client=SimpleNamespace(),
        _inbound_tasks=set(),
        _dedup={},
    )

    class Request:
        async def get_data(self):
            return (
                b'{"appid":"app","wxid":"wxid_bot","msgType":"TEXT",'
                b'"content":"hello","fromUser":"wxid_user","toUser":"wxid_bot",'
                b'"newMsgId":101,"isSelf":false}'
            )

    app = Quart(__name__)
    async with app.app_context():
        first = await adapter.handle_unified_webhook('bot-uuid', 'gewe/path-secret', Request())
        duplicate = await adapter.handle_unified_webhook('bot-uuid', 'gewe/path-secret', Request())
        invalid_path = await adapter.handle_unified_webhook('bot-uuid', 'gewe/wrong', Request())

    await asyncio.sleep(0)
    assert first[1] == 200
    assert duplicate[1] == 200
    assert invalid_path[1] == 404
    converter.target2yiri.assert_awaited_once()
    assert adapter.callback_url().endswith('/bots/bot-uuid/gewe/path-secret')


@pytest.mark.asyncio
async def test_unified_webhook_ignores_another_appid_on_shared_token():
    adapter = gewechat_v2.GeWeV2Adapter.model_construct(
        config={'app_id': 'expected-app'},
        logger=SimpleNamespace(),
        bot_account_id='',
        listeners={},
        _client=SimpleNamespace(),
        _converter=SimpleNamespace(target2yiri=AsyncMock()),
        _bot_uuid='bot-uuid',
        _inbound_tasks=set(),
        _dedup={},
    )

    class Request:
        async def get_data(self):
            return b'{"appid":"another-app","msgType":"TEXT","newMsgId":102}'

    app = Quart(__name__)
    async with app.app_context():
        response = await adapter.handle_unified_webhook('bot-uuid', 'gewe', Request())

    assert response[1] == 200
    adapter._converter.target2yiri.assert_not_awaited()


@pytest.mark.asyncio
async def test_outbound_text_mentions_use_v2_ats_field_and_voice_uses_milliseconds():
    outbound = SimpleNamespace(
        post_text=AsyncMock(),
        post_voice=AsyncMock(),
    )
    adapter = gewechat_v2.GeWeV2Adapter.model_construct(
        config={'app_id': 'app'},
        logger=SimpleNamespace(),
        bot_account_id='wxid_bot',
        listeners={},
        _client=outbound,
        _converter=SimpleNamespace(),
        _bot_uuid='',
        _inbound_tasks=set(),
        _dedup={},
    )

    await adapter.send_message(
        'group',
        'room@chatroom',
        platform_message.MessageChain(
            [
                platform_message.At(target='wxid_user', display='小明'),
                platform_message.Plain(text='你好'),
            ]
        ),
    )
    await adapter.send_message(
        'person',
        'wxid_user',
        platform_message.MessageChain([platform_message.Voice(url='https://media.example/a.silk', length=2)]),
    )

    outbound.post_text.assert_awaited_once_with('app', 'room@chatroom', '@小明 你好', 'wxid_user')
    outbound.post_voice.assert_awaited_once_with('app', 'wxid_user', 'https://media.example/a.silk', 2000)
