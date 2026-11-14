import type { On } from 'claude-code'
import { expect, test } from 'claude-code/testing'
import type { Plugin } from 'claude-code/testing'

// 测试环境不自带 $.theme，用一个内联插件补上。它的函数在 mod 的环境里运行、拿不到测试里的变量，
// 所以主题有没有设上，要从后面的状态栏调用和命令回复里间接判断
const themeHost: Plugin = {
  name: 'theme-host',
  tier: 'builtin',
  register: on => {
    on('engine.create', async ($, e, next) => {
      const built = await next(e)

      return {
        ...built,
        theme: {
          set: async () => ({ isDrawn: true, requestId: 'look' }),
          clear: async () => ({ isDrawn: false, reason: 'cleared' }),
        },
      } as typeof built
    })
  },
}
const withTheme = { plugins: [themeHost] }

// 测试里 on 挂的 hook 代表引擎本身：应答 $.store、$.ui.status 等调用并记录下来。
// store 记开关（enabled），variant 记选的哪一套人物
const fakeEngine = (on: On, stored?: boolean, variant?: string) => {
  const seen = { store: stored as unknown, variant: variant as unknown, status: [] as (string | undefined)[] }

  on('command.register', () => ({ value: { command: 'gpt-violet' } }))
  on('store.get', (_, e) => ({ value: e.key === 'variant' ? seen.variant : seen.store }))
  on('store.set', (_, e) => {
    if (e.key === 'variant') {
      seen.variant = e.value
    } else {
      seen.store = e.value
    }

    return { value: undefined }
  })
  on('ui.status', (_, e) => {
    seen.status.push(e.text)

    return { value: undefined }
  })
  on('session.start', (_, e) => ({ cwd: e.cwd }))

  return seen
}

const start = { cwd: '/', surface: 'desktop', isInteractive: true } as const
const STATUS = '💜 紫龙模式 · 星月同行'
const STATUS_Q = '💜 小紫龙 · 星月同行'
// 像用户在输入框里敲命令那样运行 /gpt-violet
const typed = (args: string) =>
  ({ command: 'gpt-violet', args, origin: { kind: 'composer' }, presentation: { isFullscreen: false, columns: 80 } }) as const

test('没关过时，会话启动就套上紫龙主题', withTheme, async ($, on) => {
  const seen = fakeEngine(on)

  await $.session.start(start)
  expect(seen.status).toEqual([STATUS])
})

test('关过以后，会话启动不套主题', withTheme, async ($, on) => {
  const seen = fakeEngine(on, false)

  await $.session.start(start)
  expect(seen.status).toEqual([])
})

test('/gpt-violet off 关掉并记住，/gpt-violet on 再打开', withTheme, async ($, on) => {
  const seen = fakeEngine(on)

  await $.session.start(start)
  const off = await $.command.run(typed('off'))

  expect(off.text).toContain('已关闭')
  expect(seen.store).toBe(false)
  expect(seen.status.at(-1)).toBe(undefined)

  const back = await $.command.run(typed(' ON '))

  expect(back.text).toContain('已打开')
  expect(seen.store).toBe(true)
  expect(seen.status.at(-1)).toBe(STATUS)
})

test('/gpt-violet q 换成 Q 版并记住，lady 换回御姐版', withTheme, async ($, on) => {
  const seen = fakeEngine(on)

  await $.session.start(start)
  const q = await $.command.run(typed('q'))

  expect(q.text).toContain('Q 版')
  expect(seen.variant).toBe('q')
  expect(seen.store).toBe(true)
  expect(seen.status.at(-1)).toBe(STATUS_Q)

  const lady = await $.command.run(typed('Lady'))

  expect(lady.text).toContain('御姐版')
  expect(seen.variant).toBe('lady')
  expect(seen.status.at(-1)).toBe(STATUS)
})

test('关着的时候换成 Q 版，会顺手打开主题', withTheme, async ($, on) => {
  const seen = fakeEngine(on, false)

  await $.session.start(start)
  expect(seen.status).toEqual([])
  await $.command.run(typed('q'))
  expect(seen.store).toBe(true)
  expect(seen.status).toEqual([STATUS_Q])
})

test('选过 Q 版的，新会话启动就是 Q 版', withTheme, async ($, on) => {
  const seen = fakeEngine(on, undefined, 'q')

  await $.session.start(start)
  expect(seen.status).toEqual([STATUS_Q])
})

test('不带参数在开和关之间切换', withTheme, async ($, on) => {
  const seen = fakeEngine(on)

  await $.session.start(start)
  await $.command.run(typed(''))
  expect(seen.store).toBe(false)
  await $.command.run(typed(''))
  expect(seen.store).toBe(true)
})

test('参数写错只给用法，不动开关', withTheme, async ($, on) => {
  const seen = fakeEngine(on)

  await $.session.start(start)
  const wrong = await $.command.run(typed('maybe'))

  expect(wrong.text).toContain('用法')
  expect(seen.store).toBe(undefined)
  expect(seen.status).toEqual([STATUS])
})

test('紫龙只画自己的场景，别人的背景交回给引擎', async ($, on) => {
  on('ui.render', ($, e) => {
    const { Text } = $.ui.resolve(e)

    return <Text>engine</Text>
  })

  for (const surface of ['desktop', 'mobile'] as const) {
    const chibi = await $.ui.mount({
      plugin: 'gpt-violet',
      surface,
      component: 'Backdrop',
      props: { scene: 'violet-chibi', plugin: 'gpt-violet', glass: false },
    })

    expect(await chibi.find({ type: 'Text', text: /engine/ })).toBe(undefined)
    expect(await chibi.findAll({ type: 'Svg' })).toHaveLength(1)
    await chibi.unmount()

    const other = await $.ui.mount({
      plugin: 'gpt-violet',
      surface,
      component: 'Backdrop',
      props: { scene: 'lagoon', plugin: 'other', glass: false },
    })

    expect((await other.find({ type: 'Text', text: /engine/ }))?.text).toBe('engine')
    await other.unmount()

    const own = await $.ui.mount({
      plugin: 'gpt-violet',
      surface,
      component: 'Backdrop',
      props: { scene: 'violet-dragon', plugin: 'gpt-violet', glass: { blur: 3, opacity: 0.4 } },
    })

    expect(await own.find({ type: 'Text', text: /engine/ })).toBe(undefined)
    await own.unmount()
  }
})

test('桌面端和手机端都只画一张 SVG（拆成多张叠画时桌面端会整张留空）', async $ => {
  const own = { scene: 'violet-dragon', plugin: 'gpt-violet', glass: false } as const

  for (const surface of ['desktop', 'mobile'] as const) {
    const ui = await $.ui.mount({ plugin: 'gpt-violet', surface, component: 'Backdrop', props: own })

    expect(await ui.findAll({ type: 'Svg' })).toHaveLength(1)
    await ui.unmount()
  }
})

test('场景名放在旧字段 id 里时，紫龙照样画自己的、放过别人的', async ($, on) => {
  on('ui.render', ($, e) => {
    const { Text } = $.ui.resolve(e)

    return <Text>engine</Text>
  })

  // 旧版客户端只带 id、不带 scene，按引擎的类型写不出来，所以这里绕过类型
  const backdrop = (props: Record<string, unknown>) =>
    ({ plugin: 'gpt-violet', surface: 'desktop', component: 'Backdrop', props }) as unknown as Parameters<typeof $.ui.mount>[0]

  const own = await $.ui.mount(backdrop({ id: 'violet-dragon', plugin: 'gpt-violet', glass: false }))

  expect(await own.find({ type: 'Text', text: /engine/ })).toBe(undefined)
  await own.unmount()

  const other = await $.ui.mount(backdrop({ id: 'lagoon', plugin: 'other', glass: false }))

  expect((await other.find({ type: 'Text', text: /engine/ }))?.text).toBe('engine')
  await other.unmount()
})
