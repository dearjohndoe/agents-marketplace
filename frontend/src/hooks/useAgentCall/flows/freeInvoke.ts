import { invokeFree } from '../../../lib/agentClient'
import type { InvokeResult } from '../../../lib/agentClient'
import type { FlowResult } from '../types'

export async function runFreeInvoke(args: {
  endpoint: string
  capability: string
  body: Record<string, string | number | boolean>
  skuId: string
  fileFields: Record<string, File>
}): Promise<FlowResult<InvokeResult>> {
  try {
    const res = await invokeFree(
      args.endpoint,
      args.capability,
      args.body,
      args.skuId,
      args.fileFields,
    )
    return { kind: 'ok', value: res }
  } catch (err: any) {
    const code = err?.response?.data?.error
    let message = code ?? err?.message ?? 'Failed to call agent'
    if (code === 'free_limit_reached') message = 'Бесплатная попытка уже использована'
    else if (code === 'out_of_stock') message = 'Бесплатные выдачи закончились'
    return { kind: 'error', message }
  }
}
