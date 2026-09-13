/**
 * 消息盒子数据源（导航栏铃铛）。
 *
 * 原模板从 @/api/message 拉模拟消息；面板后端没有站内信概念，
 * 这里改为本地静态数据 —— 后续可对接面板审计日志或自愈事件做真实通知。
 */

export interface MessageRecord {
  id: number;
  type: 'message' | 'notice' | 'todo';
  title: string;
  subTitle?: string;
  content?: string;
  avatar?: string;
  time?: string;
  status?: number;
  /** 模板 list.vue 用的状态标签字段（0 未开始 / 1 已开通 / 2 进行中） */
  messageType?: number;
}

export type MessageListType = MessageRecord[];

export const MESSAGE_LIST: MessageListType = [
  {
    id: 1,
    type: 'notice',
    messageType: 1,
    title: '崩溃自愈已就绪',
    subTitle: '面板启动时自动布防',
    time: '启动时',
    status: 1,
  },
  {
    id: 2,
    type: 'message',
    messageType: 2,
    title: '安全提示',
    subTitle: '请尽快修改初始密码 HuanmengPanel@2026',
    time: '首次登录',
    status: 1,
  },
];

export default {
  listApi(): Promise<MessageListType> {
    return Promise.resolve(MESSAGE_LIST);
  },
};

/** 兼容模板组件的接口名：全部已读（本地数据无持久化） */
export function queryMessageList(): Promise<{ data: MessageListType }> {
  return Promise.resolve({ data: MESSAGE_LIST });
}

export function setMessageStatus(_params: { ids: number[] }): Promise<{ data: { ok: true } }> {
  return Promise.resolve({ data: { ok: true } });
}
