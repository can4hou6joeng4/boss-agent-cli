"""聊天操作工具 - 封装聊天消息发送、历史获取等操作"""

from typing import Any

from boss_agent_cli.api.client import BossClient


class ChatTools:
    """聊天相关操作工具类"""

    def __init__(self, client: BossClient):
        self.client = client

    def get_chat_history(self, gid: str, security_id: str, page: int = 1, count: int = 20) -> dict[str, Any]:
        """获取聊天历史

        Args:
            gid: 会话ID
            security_id: 安全ID
            page: 页码
            count: 每页数量

        Returns:
            聊天历史
        """
        return self.client.chat_history(gid, security_id, page=page, count=count)

    def get_friend_list(self, page: int = 1) -> dict[str, Any]:
        """获取好友列表

        Args:
            page: 页码

        Returns:
            好友列表
        """
        return self.client.friend_list(page=page)

    def send_message(self, gid: str, security_id: str, message: str) -> dict[str, Any]:
        """发送聊天消息

        注意：当前API层可能没有直接发送消息的接口，
        这个方法需要通过浏览器客户端实现

        Args:
            gid: 会话ID
            security_id: 安全ID
            message: 消息内容

        Returns:
            发送结果
        """
        # TODO: 需要通过浏览器客户端实现
        raise NotImplementedError("发送消息功能需要通过浏览器客户端实现")

    def upload_attachment(self, gid: str, file_path: str) -> dict[str, Any]:
        """上传附件

        注意：当前API层没有上传附件的接口，
        这个方法需要通过浏览器客户端实现

        Args:
            gid: 会话ID
            file_path: 文件路径

        Returns:
            上传结果
        """
        # TODO: 需要通过浏览器客户端实现
        raise NotImplementedError("上传附件功能需要通过浏览器客户端实现")

    def exchange_contact(self, security_id: str, uid: str, name: str, exchange_type: int = 1) -> dict[str, Any]:
        """请求交换联系方式

        Args:
            security_id: 安全ID
            uid: 用户唯一ID
            name: 姓名
            exchange_type: 交换类型（1=手机, 2=微信）

        Returns:
            操作结果
        """
        return self.client.exchange_contact(security_id, uid, name, exchange_type)
