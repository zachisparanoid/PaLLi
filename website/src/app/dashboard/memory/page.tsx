import { Navbar } from "@/components/site/navbar";
import { MemoryGalaxy } from "@/components/dashboard/v2/MemoryGalaxy";

export default function DashboardMemoryPage() {
  return (
    <div className="min-h-screen bg-[color:var(--bg)] text-white flex flex-col">
      <Navbar />
      <main className="flex-1 min-h-0 px-4 py-6">
        <div className="mx-auto h-[calc(100vh-9rem)] w-full max-w-[1600px]">
          <MemoryGalaxy />
        </div>
      </main>
    </div>
  );
}
