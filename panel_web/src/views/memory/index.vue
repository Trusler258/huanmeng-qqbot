<template>
  <div class="container">
    <Breadcrumb :items="['menu.memory', 'menu.memory.notes']" />
    <div class="layout">
      <a-tabs type="card" size="medium">
        <!-- ══ 笔记本 ══ -->
        <a-tab-pane key="notes" title="笔记本">
          <a-grid :cols="24" :col-gap="16">
            <a-grid-item :span="6">
              <a-card class="general-card" title="会话列表" :loading="notesLoading">
                <div
                  v-for="n in notesList"
                  :key="n.chat_id"
                  class="pick-item"
                  :class="{ active: n.chat_id === noteChat }"
                  @click="selectNote(n.chat_id)"
                >
                  {{ n.chat_id }}
                  <span class="pick-meta">{{ n.lines ?? '' }} 条</span>
                </div>
                <a-empty v-if="!notesList.length" description="暂无笔记本" />
              </a-card>
            </a-grid-item>
            <a-grid-item :span="18">
              <a-card class="general-card" :title="noteChat ? `笔记 ${noteChat}` : '选择会话'">
                <template #extra>
                  <a-input-search
                    v-if="noteChat"
                    v-model="newNoteText"
                    placeholder="追加一条笔记，回车提交"
                    style="width: 320px"
                    @search="addNote"
                  />
                </template>
                <a-list :data="noteLines" :bordered="false" :loading="noteLoading">
                  <template #item="{ item, index }">
                    <div class="note-line">
                      <span class="note-idx">{{ index + 1 }}.</span>
                      <span class="note-text">{{ item }}</span>
                      <a-space size="mini">
                        <a-button size="mini" type="text" @click="editNote(index, item)">改</a-button>
                        <a-popconfirm content="删除这条笔记？" @ok="delNote(index)">
                          <a-button size="mini" type="text" status="danger">删</a-button>
                        </a-popconfirm>
                      </a-space>
                    </div>
                  </template>
                  <template #empty><a-empty description="选择左侧会话查看笔记" /></template>
                </a-list>
              </a-card>
            </a-grid-item>
          </a-grid>
        </a-tab-pane>

        <!-- ══ 短期记忆 ══ -->
        <a-tab-pane key="stm" title="短期记忆">
          <a-grid :cols="24" :col-gap="16">
            <a-grid-item :span="6">
              <a-card class="general-card" title="会话列表" :loading="stmLoading">
                <div
                  v-for="s in stmList"
                  :key="s.chat_id"
                  class="pick-item"
                  :class="{ active: s.chat_id === stmChat }"
                  @click="selectStm(s.chat_id)"
                >
                  {{ s.chat_id }}
                </div>
                <a-empty v-if="!stmList.length" description="暂无会话" />
              </a-card>
            </a-grid-item>
            <a-grid-item :span="18">
              <a-card class="general-card" :title="stmChat ? `短期记忆 ${stmChat}` : '选择会话'">
                <a-list :data="stmRows" :bordered="false" :loading="stmDetailLoading">
                  <template #item="{ item }">
                    <div class="stm-row">
                      <span class="stm-role" :class="item.role">{{ item.role }}</span>
                      <span class="stm-text">{{ item.content || item.text }}</span>
                    </div>
                  </template>
                  <template #empty><a-empty description="选择左侧会话查看" /></template>
                </a-list>
              </a-card>
            </a-grid-item>
          </a-grid>
        </a-tab-pane>

        <!-- ══ 自认知 ══ -->
        <a-tab-pane key="self" title="自认知文档">
          <a-card class="general-card" title="data/self_knowledge.md">
            <pre class="doc-pre">{{ selfKnowledge || '加载中...' }}</pre>
          </a-card>
        </a-tab-pane>

        <!-- ══ 技能提示词 ══ -->
        <a-tab-pane key="skills" title="技能提示词">
          <a-grid :cols="24" :col-gap="16">
            <a-grid-item :span="6">
              <a-card class="general-card" title="data/skills/" :loading="skillsLoading">
                <div
                  v-for="sk in skillsList"
                  :key="sk.name"
                  class="pick-item"
                  :class="{ active: sk.name === skillName }"
                  @click="selectSkill(sk.name)"
                >
                  {{ sk.name }}
                </div>
                <a-empty v-if="!skillsList.length" description="暂无技能文件" />
              </a-card>
            </a-grid-item>
            <a-grid-item :span="18">
              <a-card class="general-card" :title="skillName || '选择技能文件'">
                <pre class="doc-pre">{{ skillContent || '选择左侧文件查看' }}</pre>
              </a-card>
            </a-grid-item>
          </a-grid>
        </a-tab-pane>
      </a-tabs>
    </div>
  </div>
</template>

<script lang="ts" setup>
  import { ref, onMounted } from 'vue';
  import { Message, Modal } from '@arco-design/web-vue';
  import Breadcrumb from '@/components/breadcrumb/index.vue';
  import useLoading from '@/hooks/loading';
  import {
    getNotesList,
    getNote,
    addNoteLine,
    updateNoteLine,
    deleteNoteLine,
    getStmList,
    getStm,
    getSelfKnowledge,
    getSkillsList,
    getSkillFile,
  } from '@/api/panel';

  const { loading: notesLoading, setLoading: setNotesLoading } = useLoading();
  const { loading: noteLoading, setLoading: setNoteLoading } = useLoading();
  const { loading: stmLoading, setLoading: setStmLoading } = useLoading();
  const { loading: stmDetailLoading, setLoading: setStmDetailLoading } = useLoading();
  const { loading: skillsLoading, setLoading: setSkillsLoading } = useLoading();

  // notes
  const notesList = ref<any[]>([]);
  const noteChat = ref('');
  const noteLines = ref<string[]>([]);
  const newNoteText = ref('');

  // stm
  const stmList = ref<any[]>([]);
  const stmChat = ref('');
  const stmRows = ref<any[]>([]);

  // self-knowledge / skills
  const selfKnowledge = ref('');
  const skillsList = ref<any[]>([]);
  const skillName = ref('');
  const skillContent = ref('');

  const loadNotesList = async () => {
    setNotesLoading(true);
    try {
      const res = await getNotesList();
      notesList.value = res.data.files || res.data.notes || [];
    } finally {
      setNotesLoading(false);
    }
  };
  const selectNote = async (chatId: string) => {
    noteChat.value = chatId;
    setNoteLoading(true);
    try {
      const res = await getNote(chatId);
      noteLines.value = res.data.lines || [];
    } finally {
      setNoteLoading(false);
    }
  };
  const addNote = async () => {
    if (!noteChat.value || !newNoteText.value.trim()) return;
    await addNoteLine(noteChat.value, newNoteText.value.trim());
    Message.success('已追加');
    newNoteText.value = '';
    await selectNote(noteChat.value);
  };
  const editNote = (index: number, text: string) => {
    Modal.prompt?.({
      title: '修改笔记',
      content: '直接编辑内容',
      defaultValue: text,
      onOk: async (v: string) => {
        await updateNoteLine(noteChat.value, index, v);
        Message.success('已修改');
        await selectNote(noteChat.value);
      },
    }) ?? simpleEdit(index, text);
  };
  // Arco 无 Modal.prompt 时用简易实现
  const simpleEdit = (index: number, text: string) => {
    const input = window.prompt('修改笔记内容', text);
    if (input === null) return;
    updateNoteLine(noteChat.value, index, input).then(async () => {
      Message.success('已修改');
      await selectNote(noteChat.value);
    });
  };
  const delNote = async (index: number) => {
    await deleteNoteLine(noteChat.value, index);
    Message.success('已删除');
    await selectNote(noteChat.value);
  };

  const loadStmList = async () => {
    setStmLoading(true);
    try {
      const res = await getStmList();
      stmList.value = res.data.chats || res.data.files || [];
    } finally {
      setStmLoading(false);
    }
  };
  const selectStm = async (chatId: string) => {
    stmChat.value = chatId;
    setStmDetailLoading(true);
    try {
      const res = await getStm(chatId, { limit: 100 });
      stmRows.value = res.data.messages || res.data.rows || [];
    } finally {
      setStmDetailLoading(false);
    }
  };

  const loadSelfKnowledge = async () => {
    try {
      const res = await getSelfKnowledge();
      selfKnowledge.value = res.data.content || res.data.raw || '';
    } catch {
      selfKnowledge.value = '(加载失败)';
    }
  };

  const loadSkillsList = async () => {
    setSkillsLoading(true);
    try {
      const res = await getSkillsList();
      skillsList.value = res.data.files || res.data.skills || [];
    } finally {
      setSkillsLoading(false);
    }
  };
  const selectSkill = async (name: string) => {
    skillName.value = name;
    try {
      const res = await getSkillFile(name);
      skillContent.value = res.data.content || res.data.raw || '';
    } catch {
      skillContent.value = '(加载失败)';
    }
  };

  onMounted(async () => {
    await Promise.all([loadNotesList(), loadStmList(), loadSelfKnowledge(), loadSkillsList()]);
  });
</script>

<script lang="ts">
  export default { name: 'MemoryPage' };
</script>

<style lang="less" scoped>
  .container {
    padding: 0 20px 20px;
  }
  .layout {
    margin-top: 16px;
  }
  .pick-item {
    padding: 7px 10px;
    border-radius: 6px;
    cursor: pointer;
    font-size: 13px;
    transition: background 0.2s;
    &:hover {
      background: var(--color-fill-2);
    }
    &.active {
      background: var(--color-primary-light-1);
    }
  }
  .pick-meta {
    color: var(--color-text-3);
    font-size: 12px;
    margin-left: 6px;
  }
  .note-line {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    padding: 4px 0;
    border-bottom: 1px dashed var(--color-border-1);
  }
  .note-idx {
    color: var(--color-text-3);
    min-width: 28px;
    text-align: right;
  }
  .note-text {
    flex: 1;
    word-break: break-all;
    white-space: pre-wrap;
  }
  .stm-row {
    display: flex;
    gap: 8px;
    padding: 3px 0;
    align-items: flex-start;
  }
  .stm-role {
    flex: 0 0 auto;
    font-size: 12px;
    padding: 0 6px;
    border-radius: 4px;
    background: var(--color-fill-2);
    &.user {
      color: var(--color-primary-6);
    }
    &.assistant {
      color: var(--color-success-6);
    }
  }
  .stm-text {
    word-break: break-all;
    white-space: pre-wrap;
  }
  .doc-pre {
    margin: 0;
    font-family: 'JetBrains Mono', Consolas, monospace;
    font-size: 13px;
    line-height: 1.7;
    white-space: pre-wrap;
    word-break: break-all;
    max-height: 640px;
    overflow: auto;
  }
</style>
