"use client";

import { useEffect, useRef } from "react";
import { useTheme } from "@/contexts/ThemeContext";

/**
 * App-wide ambient background: small glowing particles drifting upward.
 *
 * Rendered once in the root layout (src/app/layout.tsx) as a fixed,
 * full-viewport canvas behind all page content. Pages must therefore NOT
 * paint an opaque background on their root element — <body> already
 * paints var(--bg-main), and an opaque page root would hide the canvas.
 */

interface Particle {
  x: number;
  y: number;
  size: number;
  speedY: number;
  speedX: number;
  colorIndex: number;
  life: number;
  maxLife: number;
}

// Each entry is an "rgba(r, g, b," prefix; alpha is appended at draw time.
const PALETTES = {
  dark: [
    "rgba(255, 255, 255,", // white glow
    "rgba(228, 228, 231,", // silver zinc
    "rgba(161, 161, 170,", // medium zinc
  ],
  light: [
    "rgba(74, 44, 23,", // rich chocolate brown
    "rgba(110, 71, 50,", // warm chestnut
    "rgba(150, 111, 87,", // warm beige sand
  ],
} as const;

const SPAWN_INTERVAL_MS = 150;

export default function ParticleBackground() {
  const { theme } = useTheme();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  // Read by the animation loop on every frame, so a theme toggle recolors
  // all particles immediately without restarting the loop.
  const paletteRef = useRef<readonly string[]>(PALETTES.dark);

  useEffect(() => {
    paletteRef.current = PALETTES[theme];
  }, [theme]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    let particles: Particle[] = [];
    let frame = 0;
    let lastSpawn = 0;

    const resize = () => {
      canvas.width = window.innerWidth;
      canvas.height = window.innerHeight;
    };
    resize();
    window.addEventListener("resize", resize);

    const spawnParticle = () => {
      particles.push({
        x: Math.random() * canvas.width,
        y: canvas.height + 10,
        size: Math.random() * 2.2 + 1,
        speedY: -(Math.random() * 0.5 + 0.2),
        speedX: (Math.random() - 0.5) * 0.25,
        colorIndex: Math.floor(Math.random() * 3),
        life: 0,
        maxLife: Math.random() * 220 + 160,
      });
    };

    const animate = (timestamp: number) => {
      ctx.clearRect(0, 0, canvas.width, canvas.height);

      if (timestamp - lastSpawn > SPAWN_INTERVAL_MS) {
        spawnParticle();
        lastSpawn = timestamp;
      }

      particles = particles.filter((p) => p.life < p.maxLife);
      const palette = paletteRef.current;

      for (const p of particles) {
        p.life++;
        p.x += p.speedX;
        p.y += p.speedY;

        // Fade in over the first 20% of life, fade out over the last 20%.
        const progress = p.life / p.maxLife;
        const opacity =
          progress < 0.2 ? progress / 0.2 : progress > 0.8 ? (1 - progress) / 0.2 : 1;
        const color = palette[p.colorIndex];

        ctx.beginPath();
        ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
        ctx.fillStyle = `${color}${opacity * 0.45})`;
        ctx.fill();

        ctx.beginPath();
        ctx.arc(p.x, p.y, p.size * 2, 0, Math.PI * 2);
        const gradient = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, p.size * 2);
        gradient.addColorStop(0, `${color}${opacity * 0.2})`);
        gradient.addColorStop(1, `${color}0)`);
        ctx.fillStyle = gradient;
        ctx.fill();
      }

      frame = requestAnimationFrame(animate);
    };

    const start = () => {
      cancelAnimationFrame(frame);
      if (reduceMotion.matches) {
        particles = [];
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        return;
      }
      frame = requestAnimationFrame(animate);
    };
    start();
    reduceMotion.addEventListener("change", start);

    return () => {
      window.removeEventListener("resize", resize);
      reduceMotion.removeEventListener("change", start);
      cancelAnimationFrame(frame);
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden
      className="fixed inset-0 z-0 w-screen h-screen pointer-events-none"
      style={{ opacity: 0.5 }}
    />
  );
}
