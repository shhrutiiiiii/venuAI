"use client";

import { Sun, Moon } from "lucide-react";
import { useTheme } from "@/contexts/ThemeContext";

interface ThemeToggleProps {
  className?: string;
  showLabel?: boolean;
}

export default function ThemeToggle({ className = "", showLabel = false }: ThemeToggleProps) {
  const { theme, toggleTheme } = useTheme();
  const handleClick = () => {
    const root = document.documentElement;
    root.classList.add("m-theme-switching");
    toggleTheme();
    window.setTimeout(() => root.classList.remove("m-theme-switching"), 350);
  };

  return (
    <button
      id="theme-toggle-btn"
      type="button"
      onClick={handleClick}
      className={`relative inline-flex items-center gap-2 px-3 py-1.5 rounded-full border m-press shadow-sm ${
        theme === "dark"
          ? "bg-slate-900/80 border-slate-700/80 text-cyan-300 hover:bg-slate-800 hover:border-slate-600"
          : "bg-white/90 border-slate-200 text-sky-600 hover:bg-slate-100 hover:border-slate-300"
      } ${className}`}
      aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
      title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
    >
      <div className="relative w-4 h-4 flex items-center justify-center">
        <Sun
          className={`w-4 h-4 transition-all duration-300 absolute ${
            theme === "dark" ? "rotate-90 scale-0 opacity-0" : "rotate-0 scale-100 opacity-100"
          }`}
        />
        <Moon
          className={`w-4 h-4 transition-all duration-300 absolute ${
            theme === "dark" ? "rotate-0 scale-100 opacity-100" : "-rotate-90 scale-0 opacity-0"
          }`}
        />
      </div>

      {showLabel && (
        <span className="text-xs font-semibold select-none">
          {theme === "dark" ? "Dark" : "Light"}
        </span>
      )}
    </button>
  );
}
