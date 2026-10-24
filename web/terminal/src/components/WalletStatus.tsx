import { Wallet } from "lucide-react";
import { useEffect, useState } from "react";
import { short, toast } from "../lib/util";

// The owner's own browser wallet (EIP-1193). Read-only here: it shows who is connected and on
// which chain. AI employees never get this wallet; result hashes are anchored by the server's
// testnet wallet after a human approval.

const TESTNET = { chainId: "0x61", chainName: "BNB Smart Chain Testnet", nativeCurrency: { name: "tBNB", symbol: "tBNB", decimals: 18 },
  rpcUrls: ["https://data-seed-prebsc-1-s1.bnbchain.org:8545"], blockExplorerUrls: ["https://testnet.bscscan.com"] };

type Eth = { request: (a: { method: string; params?: unknown[] }) => Promise<any>; on?: (e: string, f: (x: any) => void) => void; removeListener?: (e: string, f: (x: any) => void) => void };
const eth = () => (window as unknown as { ethereum?: Eth }).ethereum;

export function useWallet() {
  const [account, setAccount] = useState<string | null>(null);
  const [chainId, setChainId] = useState<string | null>(null);
  useEffect(() => {
    const p = eth();
    if (!p) return;
    p.request({ method: "eth_accounts" }).then((a: string[]) => setAccount(a[0] || null)).catch(() => undefined);
    p.request({ method: "eth_chainId" }).then(setChainId).catch(() => undefined);
    const onAcc = (a: string[]) => setAccount(a[0] || null);
    p.on?.("accountsChanged", onAcc);
    p.on?.("chainChanged", setChainId);
    return () => {
      p.removeListener?.("accountsChanged", onAcc);
      p.removeListener?.("chainChanged", setChainId);
    };
  }, []);
  const connect = async () => {
    const p = eth();
    if (!p) return toast("没有检测到浏览器钱包（MetaMask、OKX、Binance Wallet 等）", true);
    try {
      const a: string[] = await p.request({ method: "eth_requestAccounts" });
      setAccount(a[0] || null);
      setChainId(await p.request({ method: "eth_chainId" }));
    } catch (e: any) {
      toast(e?.message || "连接被拒绝", true);
    }
  };
  const switchTestnet = async () => {
    const p = eth();
    if (!p) return;
    try {
      await p.request({ method: "wallet_switchEthereumChain", params: [{ chainId: TESTNET.chainId }] });
    } catch (e: any) {
      if (e?.code === 4902) await p.request({ method: "wallet_addEthereumChain", params: [TESTNET] }).catch((x: any) => toast(x?.message || "添加网络失败", true));
      else toast(e?.message || "切换网络失败", true);
    }
  };
  return { account, chainId, connect, switchTestnet, available: !!eth() };
}

export function WalletButton() {
  const w = useWallet();
  if (!w.account)
    return (
      <button className="btn" onClick={w.connect}>
        <Wallet size={14} />
        <span className="d-only">Connect</span>
      </button>
    );
  const wrong = w.chainId !== TESTNET.chainId;
  return (
    <button className={`btn ${wrong ? "danger" : ""}`} onClick={wrong ? w.switchTestnet : undefined} title={wrong ? "当前不是 BSC 测试网，点击切换" : w.account}>
      <span className={`badge ${wrong ? "bad" : "ok"}`} style={{ height: 16, padding: "0 4px" }}>
        <span className="dot" />
        {wrong ? `chain ${parseInt(w.chainId || "0", 16)}` : "tBSC"}
      </span>
      <span className="mono">{short(w.account, 4)}</span>
    </button>
  );
}
