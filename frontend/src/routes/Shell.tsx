import { useEffect, useState } from "react"
import { Link, Outlet } from "react-router"
import { Moon, ShieldCheck, Sun } from "lucide-react"

import { api } from "../api/client"
import type { Learner } from "../api/types"

type Theme = "light" | "dark" | "system"

function storedTheme(): Theme {
  const saved = localStorage.getItem("theme")
  return saved === "light" || saved === "dark" ? saved : "system"
}

export function Shell() {
  const [learner, setLearner] = useState<Learner | null>(null)
  const [theme, setTheme] = useState<Theme>(storedTheme)

  useEffect(() => {
    api.me().then(setLearner).catch(() => setLearner(null))
  }, [])

  useEffect(() => {
    const root = document.documentElement
    if (theme === "system") {
      root.removeAttribute("data-theme")
      localStorage.removeItem("theme")
    } else {
      root.setAttribute("data-theme", theme)
      localStorage.setItem("theme", theme)
    }
  }, [theme])

  return (
    <div className="min-h-full flex flex-col">
      <header className="border-b border-line bg-surface">
        <div className="mx-auto flex max-w-5xl items-center gap-3 px-5 py-3">
          <Link to="/" className="flex items-center gap-2 font-semibold">
            <ShieldCheck size={20} className="text-accent" aria-hidden />
            <span>Security Awareness</span>
          </Link>
          <div className="ml-auto flex items-center gap-4 text-sm">
            {learner && (
              <span className="text-muted hidden sm:inline">
                {learner.display_name || learner.email}
              </span>
            )}
            {learner && (
              <a href="/auth/logout" className="text-muted hover:text-text">
                Sign out
              </a>
            )}
            <button
              type="button"
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
              className="text-muted hover:text-text"
              aria-label={theme === "dark" ? "Use the light theme"
                                           : "Use the dark theme"}
            >
              {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
            </button>
          </div>
        </div>
      </header>
      <main className="flex-1">
        <Outlet />
      </main>
    </div>
  )
}
