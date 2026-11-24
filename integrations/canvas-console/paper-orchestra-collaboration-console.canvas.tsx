import {
  Banner,
  Button,
  Callout,
  Card,
  CardBody,
  CardHeader,
  Checkbox,
  Code,
  CollapsibleSection,
  H1,
  H2,
  H3,
  MetricsGrid,
  Pill,
  ReferencePanel,
  Row,
  Select,
  SendToChatButton,
  Stack,
  Text,
  TextArea,
  Timeline,
  useCanvasState,
  useHostTheme,
} from "qoder/canvas";

const projectTitle = "合成数据线性回归分析的可复现性演示";
const runId = "0522e68f-d46a-4c4c-92ed-7ff8b27c427f";
const runDir = "D:\\大四暑假\\paper-orchestra\\desktop_runs\\paper_20261124_000703_0522e68f";

const interventionPresets = [
  {
    label: "核查运行健康度",
    target: "整体流程",
    prompt:
      "请检查当前 PaperOrchestra 运行的最新日志和产物，重点排查提供商 URL/端口错误、PDF 生成失败和缺图警告是否影响最终交付。先给出诊断与修复建议，不要停止运行，也不要直接修改文件，等我确认。",
  },
  {
    label: "只修方法学叙述",
    target: "方法学",
    prompt:
      "当前运行请继续。只调整论文的方法学叙述，使其与已完成的实验和代码一致；保留数据、结果和结论。先列出拟修改段落和理由，等我确认后再保存。",
  },
  {
    label: "补证据与引用",
    target: "文献证据",
    prompt:
      "请检查论文中的关键论断是否有充分证据支持，优先核对参考文献与正文引用是否匹配。先给出缺口、候选文献和推荐引用位置；不要自动改写正文。",
  },
  {
    label: "评估并重做图稿",
    target: "图稿",
    prompt:
      "请盘点当前运行的图稿、对应结论和缺失图。先评估哪些图需要重做以及原因，等待我指定具体图号后再修改，避免整批返工。",
  },
  {
    label: "从检查点恢复",
    target: "运行控制",
    prompt:
      "请列出当前运行可用的检查点、已完成阶段和未完成项，推荐最合适的恢复点并说明代价。先不要执行 resume_run，等我确认。",
  },
  {
    label: "停止前先盘点",
    target: "运行控制",
    prompt:
      "请先总结当前进度、已生成产物、未保存修改和下一步计划。暂时不要停止运行；把风险点列清楚后等我决定是否停止。",
  },
];

const stageEvents = [
  {
    id: "writing",
    timestamp: "已观察",
    title: "写作",
    description: "论文正文与结构开始生成。",
    state: "completed" as const,
    tone: "success" as const,
  },
  {
    id: "review",
    timestamp: "已观察",
    title: "审阅",
    description: "进入内容检查与修改环节。",
    state: "completed" as const,
    tone: "success" as const,
  },
  {
    id: "refinement",
    timestamp: "当前",
    title: "精修",
    description: "已进入第 3/3 轮精修；v2 评分由 3 提升到 4，最终产物仍需核查。",
    state: "current" as const,
    tone: "warning" as const,
  },
];

interface ConsoleState {
  target: string;
  note: string;
  conservative: boolean;
  preview: string;
  status: string;
  history: Array<{ target: string; note: string; createdAt: string }>;
}

const defaultState: ConsoleState = {
  target: "方法学",
  note: "",
  conservative: true,
  preview: "",
  status: "",
  history: [],
};

function buildIntervention(state: ConsoleState) {
  const safety = state.conservative
    ? "请先检查并给出具体方案，不要停止当前运行，也不要直接修改文件；等我确认后再执行。"
    : "请按我的要求处理当前环节，并在执行前说明将改动哪些文件。";
  return [
    `当前项目：${projectTitle}`,
    `当前运行：${runId}`,
    `介入环节：${state.target}`,
    `我的要求：${state.note.trim()}`,
    safety,
  ].join("\n");
}

export default function PaperOrchestraCollaborationConsole() {
  const theme = useHostTheme();
  const tokens = theme.tokens;
  const [state, setState] = useCanvasState<ConsoleState>(
    "paper-orchestra-collaboration-console:v1",
    defaultState,
  );

  const prompt = state.preview || buildIntervention(state);
  const canSend = state.note.trim().length >= 4;

  return (
    <div className="po-console">
      <style>{`
        .po-console {
          --po-bg: ${tokens.bg.editor};
          --po-panel: ${tokens.bg.elevated};
          --po-subtle: ${tokens.fill.tertiary};
          --po-text: ${tokens.text.primary};
          --po-muted: ${tokens.text.secondary};
          --po-line: ${tokens.stroke.tertiary};
          --po-accent: ${tokens.accent.control};
          --po-warning: ${tokens.status.warning};
          display: flex;
          flex-direction: column;
          gap: 20px;
          color: var(--po-text);
        }
        .po-console * { box-sizing: border-box; }
        .po-kicker {
          display: flex;
          align-items: center;
          gap: 8px;
          flex-wrap: wrap;
        }
        .po-stage-rail {
          display: flex;
          align-items: center;
          gap: 8px;
          flex-wrap: wrap;
        }
        .po-stage {
          display: inline-flex;
          align-items: center;
          gap: 6px;
          padding: 5px 10px;
          border: 1px solid var(--po-line);
          border-radius: 999px;
          background: var(--po-subtle);
          font-size: 12px;
        }
        .po-stage.current {
          border-color: var(--po-warning);
        }
        .po-arrow { color: var(--po-muted); }
        .po-divider {
          height: 1px;
          background: var(--po-line);
          margin: 4px 0;
        }
        .po-preset-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
          gap: 10px;
        }
        .po-preset {
          display: flex;
          flex-direction: column;
          align-items: flex-start;
          gap: 8px;
          padding: 14px;
          border: 1px solid var(--po-line);
          border-radius: 10px;
          background: var(--po-panel);
          text-align: left;
        }
        .po-history {
          display: flex;
          flex-direction: column;
          gap: 10px;
        }
        .po-history-item {
          padding: 10px 12px;
          border-left: 3px solid var(--po-accent);
          background: var(--po-subtle);
          border-radius: 0 8px 8px 0;
        }
      `}</style>

      <Stack gap={8}>
        <Row justify="space-between" align="start" wrap gap={12}>
          <Stack gap={6}>
            <div className="po-kicker">
              <Pill tone="warning">运行中 · 精修阶段</Pill>
              <Text size="small" tone="secondary">最近检查 · 19:41</Text>
            </div>
            <H1>论文协作控制台</H1>
            <Text tone="secondary">{projectTitle}</Text>
          </Stack>
          <Text size="small" tone="secondary">实时监控请打开本机工作台</Text>
        </Row>
        <div className="po-stage-rail" aria-label="已观察运行阶段">
          <span className="po-stage">写作 · 已完成</span>
          <span className="po-arrow" aria-hidden="true">→</span>
          <span className="po-stage">审阅 · 已完成</span>
          <span className="po-arrow" aria-hidden="true">→</span>
          <span className="po-stage current">精修 · 进行中</span>
        </div>
      </Stack>

      <Callout
        type="warning"
        title="运行仍在继续，v2 PDF 已生成，最终产物仍需核查"
      >
        早期日志出现过提供商 URL/端口错误、PDF 生成失败与缺图警告；精修阶段已保存 refined_paper_v2.pdf，且 v2 评分由 3 提升到 4。建议确认最终 PDF、图稿和参考文献是否完整，再决定是否返工。
      </Callout>

      <MetricsGrid
        variant="header"
        columns={4}
        items={[
          { label: "项目", value: "6" },
          { label: "运行记录", value: "3" },
          { label: "内置技能", value: "5" },
          { label: "文献库条目", value: "0", description: "尚未保存文献" },
        ]}
      />

      <Card>
        <CardHeader title="选择介入方式" trailing={<Text size="small" tone="secondary">不会直接改动 Studio</Text>} />
        <CardBody>
          <Stack gap={12}>
            <Text tone="secondary">
              点选一个意图，或写下你的具体要求。发送按钮只会把指令放入 Qoder 对话输入框，由你检查后手动提交。
            </Text>
            <div className="po-preset-grid">
              {interventionPresets.map((preset) => (
                <div className="po-preset" key={preset.label}>
                  <Text weight="semibold">{preset.label}</Text>
                  <Text size="small" tone="secondary">{preset.target}</Text>
                  <SendToChatButton
                    label="交给助手处理"
                    text={preset.prompt}
                    options={{ submit: false, chatMode: "agent" }}
                    variant="secondary"
                  />
                </div>
              ))}
            </div>
          </Stack>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="自定义本次干预" />
        <CardBody>
          <Stack gap={12}>
            <Stack gap={6}>
              <Text weight="medium">介入环节</Text>
              <Select
                value={state.target}
                onChange={(target) => setState((prev) => ({ ...prev, target, preview: "", status: "" }))}
                options={[
                  { value: "整体流程", label: "整体流程" },
                  { value: "研究设计", label: "研究设计" },
                  { value: "数据分析", label: "数据分析" },
                  { value: "方法学", label: "方法学" },
                  { value: "文献证据", label: "文献证据" },
                  { value: "图稿", label: "图稿" },
                  { value: "LaTeX", label: "LaTeX 与排版" },
                  { value: "导出", label: "导出与提交检查" },
                  { value: "运行控制", label: "运行控制与检查点" },
                ]}
                style={{ width: "100%" }}
              />
            </Stack>
            <Stack gap={6}>
              <Text weight="medium">你对这一环节的不满或目标</Text>
              <TextArea
                value={state.note}
                onChange={(note) => setState((prev) => ({ ...prev, note, preview: "", status: "" }))}
                placeholder="例如：方法部分没有交代随机种子和划分比例；请先定位相关段落，给出修改方案。"
                rows={5}
                style={{ width: "100%" }}
              />
            </Stack>
            <Checkbox
              checked={state.conservative}
              onChange={(conservative) => setState((prev) => ({ ...prev, conservative, preview: "", status: "" }))}
              label="保守模式：保留当前运行，先检查、给方案，等我确认后再写入"
            />
            <Row gap={8} wrap>
              <Button
                variant="secondary"
                disabled={!canSend}
                onClick={() =>
                  setState((prev) => {
                    const nextPrompt = buildIntervention({ ...prev, note: prev.note.trim() });
                    const entry = {
                      target: prev.target,
                      note: prev.note.trim(),
                      createdAt: new Date().toLocaleString(),
                    };
                    return {
                      ...prev,
                      preview: nextPrompt,
                      status: "指令已准备好。你可以继续编辑，或放入对话后手动发送。",
                      history: [entry, ...prev.history].slice(0, 5),
                    };
                  })
                }
              >
                生成干预指令
              </Button>
              <Button
                variant="ghost"
                onClick={() => setState((prev) => ({ ...prev, note: "", preview: "", status: "" }))}
              >
                清空草稿
              </Button>
              <SendToChatButton
                label="放入对话 · 不自动提交"
                text={() => prompt}
                options={{ submit: false, chatMode: "agent" }}
                disabled={!canSend}
                onSent={(ok) =>
                  setState((prev) => ({
                    ...prev,
                    status: ok ? "已放入对话输入框，请检查后手动发送。" : "未能交给对话，请在本机工作台中操作。",
                  }))
                }
              />
            </Row>
            {state.status ? <Text size="small" tone="secondary">{state.status}</Text> : null}
          </Stack>
        </CardBody>
      </Card>

      <CollapsibleSection title="本次指令预览" defaultOpen={false}>
        <Stack gap={8}>
          <Code>{prompt}</Code>
          <Text size="small" tone="secondary">
            这里展示将要放入对话的完整文本；不会调用 Studio 的写入、停止或删除工具。
          </Text>
        </Stack>
      </CollapsibleSection>

      {state.history.length > 0 ? (
        <CollapsibleSection title={`最近准备的干预（${state.history.length}）`} defaultOpen={false}>
          <div className="po-history">
            {state.history.map((item, index) => (
              <div className="po-history-item" key={`${item.createdAt}-${index}`}>
                <Text size="small" weight="semibold">{item.target} · {item.createdAt}</Text>
                <Text size="small" tone="secondary">{item.note}</Text>
              </div>
            ))}
          </div>
        </CollapsibleSection>
      ) : null}

      <div className="po-divider" />

      <Stack gap={12}>
        <H2>运行证据与进度</H2>
        <Timeline events={stageEvents} density="compact" />
        <ReferencePanel
          title="最近观察"
          items={[
            {
              id: "run",
              label: "当前运行仍处于 running",
              description: "已从 writing 推进到 refinement，第 3/3 轮评分由 3 提升到 4。",
              kind: "runbook",
              meta: runId.slice(0, 8),
            },
            {
              id: "provider",
              label: "提供商连接异常",
              description: "日志出现 URL/端口解析错误与重试记录。",
              kind: "metric",
              meta: "需核查",
            },
            {
              id: "pdf",
              label: "精修已生成 v2 PDF",
              description: "早期生成失败后，19:39 保存了 refined_paper_v2.pdf；最终交付仍需检查。",
              kind: "file",
              meta: "已生成",
            },
            {
              id: "assets",
              label: "文献库与图稿库为空",
              description: "当前没有已保存文献条目或图稿 bundle；运行产物需单独检查。",
              kind: "dataset",
              meta: "快照",
            },
          ]}
        />
      </Stack>

      <CollapsibleSection title="运行标识与产物路径" defaultOpen={false}>
        <Stack gap={8}>
          <Stack gap={4}>
            <Text size="small" weight="medium">运行 ID</Text>
            <Code>{runId}</Code>
          </Stack>
          <Stack gap={4}>
            <Text size="small" weight="medium">运行目录</Text>
            <Code>{runDir}</Code>
          </Stack>
          <Banner type="info">
            此 Canvas 保存的是最近一次检查快照，不会自动刷新。运行 start-panel.cmd 可打开实时日志、71 个工具和文件操作工作台。
          </Banner>
        </Stack>
      </CollapsibleSection>
    </div>
  );
}
