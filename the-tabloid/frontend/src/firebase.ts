import { initializeApp, type FirebaseApp } from 'firebase/app'
import { getFirestore, type Firestore } from 'firebase/firestore'

const cfg = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY,
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID,
  storageBucket: import.meta.env.VITE_FIREBASE_STORAGE_BUCKET,
  appId: import.meta.env.VITE_FIREBASE_APP_ID,
}

const configured = Boolean(cfg.apiKey && cfg.projectId)

let app: FirebaseApp | null = null
let db: Firestore | null = null

if (configured) {
  app = initializeApp(cfg)
  db = getFirestore(app)
}

export const firestoreEnabled = configured
export { db }
