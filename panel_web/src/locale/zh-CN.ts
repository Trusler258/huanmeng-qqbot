import localeMessageBox from '@/components/message-box/locale/zh-CN';
import localeLogin from '@/views/login/locale/zh-CN';

import locale403 from '@/views/exception/403/locale/zh-CN';
import locale404 from '@/views/exception/404/locale/zh-CN';
import locale500 from '@/views/exception/500/locale/zh-CN';

import localeSettings from './zh-CN/settings';

export default {
  'menu.dashboard': '概览',
  'menu.exception': '异常页',
  'navbar.docs': '文档',
  'navbar.action.locale': '切换为中文',
  ...localeSettings,
  ...localeMessageBox,
  ...localeLogin,
  ...locale403,
  ...locale404,
  ...locale500,
};
