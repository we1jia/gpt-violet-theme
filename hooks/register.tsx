import type { EngineInterface, Register, ThemeSetArgs } from 'claude-code'

import { makeColors } from './make-colors.js'
import { SCENES } from './scene.js'

// 只做浅色：背景是淡紫插画（薰衣草渐变、云海、弯月星光、紫龙少女），
// 深色模式下浅底配浅字会看不清，所以用 only: 'light' 锁定浅色
// 配色取自插画：薰衣草白底、淡紫强调，红绿黄保留各自含义。
// 强调色按 Q 版小龙娘参考图从 #7c4dda 调淡到 #9474dc，makeColors 检查没有新增对比度问题
const { theme, problems } = makeColors({
  label: 'GPT violet',
  only: 'light',
  roles: { accent: '#9474dc', danger: '#d0454c', success: '#2e9d6a', warning: '#c98a1b', pro: '#a35bd8' },
  light: {
    gray: {
      anchors: {
        'surface-0': '#f3eefc',
        'surface-1': '#f8f5fe',
        'surface-2': '#fcfaff',
        'text-primary': '#2a1f40',
        'text-secondary': '#4a3d66',
        'text-muted': '#6a5d86',
      },
    },
  },
})

// 两套人物，配色、云海和弯月共用，只有右侧的人物和状态栏文案不同。
// 背景用 scene（SVG）而不是 image：当前 App 版本的图片背景不绘制，场景里嵌入的位图也会被拦掉，
// 只有纯矢量能显示。场景由 tools/build_scene.py 生成到 hooks/scene.js（每套一张），
// 人物由 tools/trace.py 从素材描摹成矢量；为省 CPU，场景是静态的。
// 每个 Svg 最多 131072 字符。试过拆成四张叠画，桌面端整张背景留空不画，所以每套只画一张。
const VARIANTS = {
  lady: {
    scene: 'violet-dragon',
    label: '御姐版',
    status: '💜 紫龙模式 · 星月同行',
    alt: '淡紫色插画：薰衣草渐变底色，底部几团蓬松的云，左下一弯紫色月牙和星光，右侧一位长着龙角、龙翼和龙尾，穿白色荷叶边裙、翘腿坐着的银紫发少女',
  },
  q: {
    scene: 'violet-chibi',
    label: 'Q 版',
    status: '💜 小紫龙 · 星月同行',
    alt: '淡紫色插画：薰衣草渐变底色，底部几团蓬松的云，左下一弯紫色月牙和星光，右侧一个长着紫色龙角、龙翼和龙尾，穿白紫蓬蓬裙、挥手笑着的 Q 版银发小女孩',
  },
} as const
type Variant = keyof typeof VARIANTS
type SceneName = keyof typeof SCENES

const lookOf = (variant: Variant): ThemeSetArgs => ({
  colors: theme,
  // glass：App 对面板透明度有固定规则（读桌面端样式确认过）：
  // - 侧栏固定铺 56% 的 tint，不读 opacity，插件只能改它的颜色和模糊；
  // - 对话区读 opacity，但开了侧栏毛玻璃材质、软件渲染等情况时至少 70%。
  // 所以 tint 用插画的淡紫，让侧栏那层和背后的画融在一起；模糊只留 2，侧栏底部的弯月还看得清；
  // opacity 0.3 让对话区透出人物，深色字压在淡紫底上仍然清楚
  backdrop: { scene: VARIANTS[variant].scene, glass: { blur: 2, tint: '#f7f2ff', opacity: 0.3 } },
})

// 开关和选的哪一套都存在 $.store 里，跨会话保留；开关从没设置过就当作开启，人物默认御姐版
const ENABLED_KEY = 'enabled'
const VARIANT_KEY = 'variant'

const readVariant = async ($: EngineInterface): Promise<Variant> => {
  const stored = await $.store.get(VARIANT_KEY)

  return stored === 'q' ? 'q' : 'lady'
}

// 套上某一套主题；返回给命令回复用的一句话
const apply = async ($: EngineInterface, variant: Variant, done: string): Promise<string> => {
  const set = await $.theme.set(lookOf(variant))

  $.ui.status(VARIANTS[variant].status)

  if (!set.isDrawn) {
    return `${done}，但当前界面暂时没画出来：${set.reason}。背景只在桌面端和手机 App 的 Code 会话里显示。`
  }

  return `${done}，以后的新会话也会自动套用。`
}

const USAGE =
  '用法：/gpt-violet q 换成 Q 版，/gpt-violet lady 换回御姐版，/gpt-violet on 打开，/gpt-violet off 关闭，只输入 /gpt-violet 在开和关之间切换。'

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    // 命令名写成字面量：校验器据此认出这个 hook 只回答自己注册的命令
    await $.command.register({
      name: 'gpt-violet',
      description: '紫龙主题：q 换成 Q 版，lady 换回御姐版，on 打开，off 关闭，不带参数切换开关',
      argumentHint: '[q|lady|on|off]',
      immediate: true,
    })

    // 关掉过就不套主题，也不清主题：会话启动时清主题会把别的插件或卡片选的主题一起抹掉
    if ((await $.store.get(ENABLED_KEY)) === false) {
      return next(e)
    }

    // 搭建阶段：如果有一行会让桌面端整套主题不上色，弹个提示
    const unpainted = problems.find(line => line.startsWith("Desktop won't paint"))

    if (unpainted !== undefined) {
      $.ui.toast(unpainted)
    }
    // 会话启动时客户端可能还没连上，isDrawn 会是 false，但连上后照样会画，所以这里不弹提示
    const variant = await readVariant($)

    await $.theme.set(lookOf(variant))
    $.ui.status(VARIANTS[variant].status)

    return next(e)
  })

  on('command.run', { command: 'gpt-violet' }, async ($, e) => {
    // 只输入 /gpt-violet 时 args 是空串；兜底 undefined，防止别的插件用 $.command.run 不带 args 调用
    const arg = (e.args ?? '').trim().toLowerCase()

    // 换人物：记住这一套，并顺手打开主题
    if (arg === 'q' || arg === 'lady') {
      await $.store.set(VARIANT_KEY, arg)
      await $.store.set(ENABLED_KEY, true)

      return { text: await apply($, arg, `已换成${VARIANTS[arg].label}紫龙主题`) }
    }
    if (arg !== '' && arg !== 'on' && arg !== 'off') {
      return { text: USAGE }
    }
    const wasOn = (await $.store.get(ENABLED_KEY)) !== false
    const turnOn = arg === '' ? !wasOn : arg === 'on'

    await $.store.set(ENABLED_KEY, turnOn)

    if (!turnOn) {
      // 主题全会话只有一套，clear 会清掉当前生效的主题，回到 App 默认外观
      await $.theme.clear()
      $.ui.status(undefined)

      return { text: '紫龙主题已关闭，已换回 App 默认外观。以后的新会话也不会自动套用，输入 /gpt-violet on 可重新打开。' }
    }
    const variant = await readVariant($)

    return { text: await apply($, variant, `紫龙主题（${VARIANTS[variant].label}）已打开`) }
  })

  on('ui.render', { component: 'Backdrop' }, ($, e, next) => {
    // 只画自己的场景：别的插件设的背景交给它自己画。
    // 场景名在新版本里叫 scene、旧版本里叫 id（官方 theme-examples 也两个都读）；
    // 两个都没有时画默认的御姐版，宁可多画也不要整张图消失
    const props = e.props as { scene?: unknown; id?: unknown }
    const name = typeof props.scene === 'string' ? props.scene : props.id
    const scene: SceneName | undefined =
      typeof name !== 'string' ? 'violet-dragon' : Object.hasOwn(SCENES, name) ? (name as SceneName) : undefined

    if (scene === undefined) {
      return next(e)
    }
    if (e.surface !== 'desktop' && e.surface !== 'mobile') {
      return next(e)
    }

    const { Svg } = $.ui.resolve(e)
    const alt = scene === 'violet-chibi' ? VARIANTS.q.alt : VARIANTS.lady.alt

    // 不开 isInteractive：整段 SVG 按静态图片绘制，场景里只有纯矢量
    return <Svg source={SCENES[scene]} alt={alt} />
  })
}
