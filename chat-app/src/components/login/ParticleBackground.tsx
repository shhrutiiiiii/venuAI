"use client";

import { useEffect, useRef } from "react";

interface Particle {
  x: number;
  y: number;
  size: number;
  speedY: number;
  speedX: number;
  opacity: number;
  color: string;
  life: number;
  maxLife: number;
}

export default function ParticleBackground() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const particlesRef = useRef<Particle[]>([]);
  const animFrameRef = useRef<number>(0);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const isDark = document.documentElement.getAttribute("data-theme") === "dark";
    const colors = isDark
      ? [
          "rgba(255, 255, 255,",   // white glow
          "rgba(228, 228, 231,",   // silver zinc
          "rgba(161, 161, 170,",   // medium zinc
        ]
      : [
          "rgba(74, 44, 23,",     // rich chocolate brown
          "rgba(110, 71, 50,",    // warm chestnut
          "rgba(150, 111, 87,",   // warm beige sand
        ];

    const resize = () => {
      canvas.width = canvas.offsetWidth;
      canvas.height = canvas.offsetHeight;
    };
    resize();
    window.addEventListener("resize", resize);

    const spawnParticle = () => {
      const color = colors[Math.floor(Math.random() * colors.length)];
      particlesRef.current.push({
        x: Math.random() * canvas.width,
        y: canvas.height + 10,
        size: Math.random() * 2.2 + 1,
        speedY: -(Math.random() * 0.5 + 0.2),
        speedX: (Math.random() - 0.5) * 0.25,
        opacity: 0,
        color,
        life: 0,
        maxLife: Math.random() * 220 + 160,
      });
    };

    let lastSpawn = 0;
    const spawnInterval = 150;

    const animate = (timestamp: number) => {
      ctx.clearRect(0, 0, canvas.width, canvas.height);

      if (timestamp - lastSpawn > spawnInterval) {
        spawnParticle();
        lastSpawn = timestamp;
      }

      particlesRef.current = particlesRef.current.filter((p) => p.life < p.maxLife);

      particlesRef.current.forEach((p) => {
        p.life++;
        p.x += p.speedX;
        p.y += p.speedY;

        const progress = p.life / p.maxLife;
        p.opacity = progress < 0.2
          ? progress / 0.2
          : progress > 0.8
          ? (1 - progress) / 0.2
          : 1;

        ctx.beginPath();
        ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
        ctx.fillStyle = `${p.color}${p.opacity * 0.45})`;
        ctx.fill();

        ctx.beginPath();
        ctx.arc(p.x, p.y, p.size * 2, 0, Math.PI * 2);
        const gradient = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, p.size * 2);
        gradient.addColorStop(0, `${p.color}${p.opacity * 0.2})`);
        gradient.addColorStop(1, `${p.color}0)`);
        ctx.fillStyle = gradient;
        ctx.fill();
      });

      animFrameRef.current = requestAnimationFrame(animate);
    };

    animFrameRef.current = requestAnimationFrame(animate);

    return () => {
      window.removeEventListener("resize", resize);
      cancelAnimationFrame(animFrameRef.current);
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      className="absolute inset-0 w-full h-full pointer-events-none"
      style={{ opacity: 0.5 }}
    />
  );
}


