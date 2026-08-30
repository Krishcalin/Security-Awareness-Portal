import { useEffect, useState } from "react"
import { Link, Outlet } from "react-router"
import { Moon, Sun } from "lucide-react"

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
      {/* Stays put while the page scrolls under it.
       *
       * `sticky` rather than `fixed`, so the header keeps taking up its own
       * row and nothing has to be padded down by exactly its height — a
       * number that would then be wrong the first time somebody changed the
       * padding here. `bg-surface` is opaque, which it has to be now that
       * content passes behind it.
       *
       * This works because nothing between here and the viewport scrolls: an
       * ancestor with `overflow` of its own would become the scroll container
       * and the header would stick to the top of THAT, which is to say it
       * would not appear to stick at all. */}
      <header className="sticky top-0 z-40 border-b border-line bg-surface">
        <div className="mx-auto flex max-w-5xl items-center gap-3 px-5 py-3">
          {/* The shield alone, not the full lockup. The lockup's navy
              wordmark and grey strapline are drawn for a light ground and all
              but vanish on the dark theme; the shield holds up on both. Its
              intrinsic size is given so the header does not reflow around it
              when it loads. */}
          <Link to="/" className="flex items-center gap-2.5 font-semibold">
            <img
              src="/media/brand/logo-mark-128.png"
              width={128}
              height={144}
              alt=""
              aria-hidden
              className="h-7 w-auto"
            />
            <span>Security Awareness</span>
          </Link>
          <div className="ml-auto flex items-center gap-4 text-sm">
            {/* Drawn only for somebody who may see it. The link is not what
                authorises the report — every one of those endpoints checks
                for itself, and returns 404 to anybody else. */}
            {learner?.role === "admin" && (
              <Link
                to="/report/security-awareness-essentials"
                className="text-muted hover:text-text"
              >
                Reporting
              </Link>
            )}
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
