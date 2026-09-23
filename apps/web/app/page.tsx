"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { tokenStore } from "@/lib/api";

export default function IndexPage() {
  const router = useRouter();

  useEffect(() => {
    router.replace(tokenStore.access ? "/overview" : "/login");
  }, [router]);

  return null;
}
