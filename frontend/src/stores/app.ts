import { create } from 'zustand'

interface AppState {
  projectId: string | null
  shotId: string | null
  setProject: (id: string | null) => void
  setShot: (id: string | null) => void
}

export const useAppStore = create<AppState>((set) => ({
  projectId: null,
  shotId: null,
  setProject: (projectId) => set({ projectId, shotId: null }),
  setShot: (shotId) => set({ shotId }),
}))
