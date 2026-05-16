import { createContext, useContext } from 'react'
import type { SetMessage } from '../types'

export type SendFn = (msg: SetMessage) => void

export const SendContext = createContext<SendFn>(() => {})

export function useSend(): SendFn {
  return useContext(SendContext)
}
