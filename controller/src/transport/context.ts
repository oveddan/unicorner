import { createContext, useContext } from 'react'
import type { OutboundMessage } from '../types'

export type SendFn = (msg: OutboundMessage) => void

export const SendContext = createContext<SendFn>(() => {})

export function useSend(): SendFn {
  return useContext(SendContext)
}
