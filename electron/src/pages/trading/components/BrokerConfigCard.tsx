/** 海外券商（老虎/富途/IB）接入配置卡：按市场可选，配置存 Trade Redis。 */
import React, { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Input, Select, Space, Tag, Typography, message } from 'antd';
import { BankOutlined, CheckCircleOutlined, ReloadOutlined } from '@ant-design/icons';
import { authService } from '../../../features/auth/services/authService';
import { SERVICE_URLS } from '../../../config/services';

const { Text } = Typography;

type BrokerKey = 'tiger' | 'futu' | 'ib';

const BROKERS_BY_MARKET: Record<string, { key: BrokerKey; label: string; desc: string }[]> = {
  HK: [
    { key: 'futu', label: '富途证券', desc: '需本机常驻 FutuOpenD 网关；港股行情强；支持模拟环境' },
    { key: 'tiger', label: '老虎证券', desc: '纯云端 API，无需网关；SIM 模拟账户可直接演练' },
    { key: 'ib', label: '盈透 IB', desc: '需常驻 IB Gateway 容器（paper 4002 / real 4001）' },
  ],
  US: [
    { key: 'tiger', label: '老虎证券', desc: '纯云端 API，无需网关；美股主力' },
    { key: 'ib', label: '盈透 IB', desc: '需常驻 IB Gateway 容器；全球市场' },
    { key: 'futu', label: '富途证券', desc: '需本机常驻 FutuOpenD 网关' },
  ],
  FUTURES: [
    { key: 'ib', label: '盈透 IB', desc: '外盘期货（CME 等）经 IB 接入；内盘期货暂不支持' },
  ],
  CRYPTO: [],
  CN: [],
};

interface FieldDef {
  name: string;
  label: string;
  sensitive?: boolean;
  placeholder: string;
}

const FIELD_DEFS: Record<BrokerKey, FieldDef[]> = {
  tiger: [
    { name: 'tiger_id', label: 'Tiger ID', placeholder: '如 TQ12345（老虎 OpenAPI 平台获取）' },
    { name: 'rsa_private_key', label: 'RSA 私钥', sensitive: true, placeholder: 'PEM 文本（-----BEGIN 开头）或服务器上的文件路径' },
    { name: 'account', label: '交易账户', placeholder: '实盘 U 开头 / 模拟 SIM 开头，如 SIM123456' },
  ],
  futu: [
    { name: 'opend_host', label: 'FutuOpenD 地址', placeholder: '127.0.0.1' },
    { name: 'opend_port', label: 'FutuOpenD 端口', placeholder: '11111' },
    { name: 'trade_pwd_md5', label: '交易密码 MD5', sensitive: true, placeholder: '交易密码的 MD5（实盘下单前自动解锁）' },
    { name: 'trade_env', label: '交易环境', placeholder: 'SIMULATE=模拟 / REAL=实盘' },
  ],
  ib: [
    { name: 'gateway_host', label: 'Gateway 地址', placeholder: '127.0.0.1' },
    { name: 'gateway_port', label: 'Gateway 端口', placeholder: '4002=模拟 / 4001=实盘' },
    { name: 'client_id', label: 'Client ID', placeholder: '7' },
  ],
};

const apiBase = `${SERVICE_URLS.API_GATEWAY}/api/v1`;

export const BrokerConfigCard: React.FC<{ market: string }> = ({ market }) => {
  const brokers = BROKERS_BY_MARKET[market.toUpperCase()] ?? [];
  const [selected, setSelected] = useState<BrokerKey | undefined>(brokers[0]?.key);
  const [values, setValues] = useState<Record<string, string>>({});
  const [configured, setConfigured] = useState<Record<string, boolean>>({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  const authHeaders = () => {
    const token = authService.getAccessToken();
    return token ? { Authorization: `Bearer ${token}` } : undefined;
  };

  const load = useCallback(async () => {
    if (!selected) return;
    setLoading(true);
    try {
      const resp = await fetch(`${apiBase}/broker-config/${selected}`, { headers: authHeaders() });
      const data = await resp.json();
      const fields = data?.fields ?? {};
      const next: Record<string, string> = {};
      const conf: Record<string, boolean> = {};
      Object.entries(fields).forEach(([key, value]) => {
        if (key.endsWith('_configured')) {
          conf[key.replace('_configured', '')] = Boolean(value);
        } else {
          next[key] = String(value ?? '');
        }
      });
      setValues(next);
      setConfigured(conf);
    } catch {
      message.error('加载券商配置失败');
    } finally {
      setLoading(false);
    }
  }, [selected]);

  useEffect(() => {
    setValues({});
    setConfigured({});
    setSelected(brokers[0]?.key);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [market]);

  useEffect(() => {
    void load();
  }, [load]);

  const save = async () => {
    if (!selected) return;
    setSaving(true);
    try {
      const payload: Record<string, string> = {};
      FIELD_DEFS[selected].forEach(({ name }) => {
        if (values[name] !== undefined && values[name] !== '') payload[name] = values[name];
      });
      const resp = await fetch(`${apiBase}/broker-config/${selected}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ values: payload }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => null);
        throw new Error(err?.detail || `HTTP ${resp.status}`);
      }
      message.success('券商配置已保存');
      await load();
    } catch (e: any) {
      message.error(e?.message || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  if (brokers.length === 0) {
    return (
      <Alert
        type="info"
        showIcon
        message="当前市场暂无支持的实盘券商通道"
        description="加密货币市场暂未接入实盘交易；A 股使用通达信/QMT 通道（切换到「通达信交易桥」页签）。"
      />
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-sm font-bold text-gray-900 flex items-center gap-1.5">
            <BankOutlined className="text-indigo-500" /> 券商实盘接入
          </div>
          <p className="text-xs text-gray-500 mt-1">
            配置保存在服务器（敏感字段只写不回显）。下单前请确认已在券商侧开通 OpenAPI 权限；
            富途需先人工登录 FutuOpenD；IB 需先启动 IB Gateway。
          </p>
        </div>
        <Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={load}>刷新</Button>
      </div>

      <div className="flex flex-wrap gap-2">
        {brokers.map(({ key, label, desc }) => (
          <button
            key={key}
            onClick={() => setSelected(key)}
            className={`px-3 py-2 rounded-xl text-xs font-bold border transition-colors text-left ${
              selected === key
                ? 'bg-indigo-50 text-indigo-700 border-indigo-300 shadow-sm'
                : 'bg-white text-gray-600 border-gray-200 hover:border-gray-300'
            }`}
          >
            <div className="flex items-center gap-1.5">
              <BankOutlined className={selected === key ? 'text-indigo-500' : 'text-gray-400'} />
              {label}
              {key === 'futu' && <Tag className="!text-[10px] !mr-0">需 FutuOpenD</Tag>}
              {key === 'ib' && <Tag className="!text-[10px] !mr-0">需 IB Gateway</Tag>}
              {key === 'tiger' && <Tag color="green" className="!text-[10px] !mr-0">免网关</Tag>}
            </div>
            <div className="text-[10px] font-normal text-gray-400 mt-0.5 max-w-[240px]">{desc}</div>
          </button>
        ))}
      </div>

      {selected && (
        <div className="rounded-2xl border border-gray-200 bg-gray-50/50 p-4 space-y-3">
          <div className="flex items-center justify-between">
            <Text strong>{brokers.find((b) => b.key === selected)?.label}</Text>
            <Text type="secondary" className="text-xs">
              {brokers.find((b) => b.key === selected)?.desc}
            </Text>
          </div>
          {FIELD_DEFS[selected].map(({ name, label, sensitive, placeholder }) => {
            const isConfigured = configured[name];
            return (
              <div key={name}>
                <div className="text-xs font-medium text-gray-600 mb-1 flex items-center gap-2">
                  {label}
                  {sensitive && isConfigured && (
                    <Tag color="green" className="!text-[10px] !mr-0">
                      <CheckCircleOutlined /> 已配置
                    </Tag>
                  )}
                </div>
                {name === 'trade_env' ? (
                  <Select
                    value={values[name] || 'SIMULATE'}
                    onChange={(v) => setValues({ ...values, [name]: v })}
                    style={{ width: 200 }}
                    options={[
                      { value: 'SIMULATE', label: 'SIMULATE（模拟）' },
                      { value: 'REAL', label: 'REAL（实盘）' },
                    ]}
                  />
                ) : (
                  <Input
                    value={values[name] ?? ''}
                    placeholder={sensitive ? `${placeholder}（已配置则留空保持不变）` : placeholder}
                    onChange={(e) => setValues({ ...values, [name]: e.target.value })}
                  />
                )}
              </div>
            );
          })}
          <div className="flex justify-end gap-2 pt-1">
            <Button onClick={() => void load()}>还原</Button>
            <Button type="primary" loading={saving} onClick={save}>保存配置</Button>
          </div>
        </div>
      )}
    </div>
  );
};

export default BrokerConfigCard;
