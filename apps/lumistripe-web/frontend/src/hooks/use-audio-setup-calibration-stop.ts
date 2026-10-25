import { createContext, useContext } from "react"

export type AudioSetupCalibrationStop = () => Promise<void>
export type AudioSetupCalibrationContextValue = {
  registerStop: (stop: AudioSetupCalibrationStop | null) => void
  stopCalibration: () => Promise<void>
}

export const AudioSetupCalibrationContext = createContext<AudioSetupCalibrationContextValue>({
  registerStop: () => undefined,
  stopCalibration: async () => undefined,
})

export function useAudioSetupCalibrationStop() {
  return useContext(AudioSetupCalibrationContext)
}
