/**
 * The campaign filter on the Leads and Runs pages. It lists the open campaigns; a
 * campaign in the URL that isn't among them (an archived one) still gets an option,
 * so the filter never shows blank.
 */
import { useAllCampaigns } from "../api/hooks";

type Props = { value: string; onChange: (value: string) => void };

export function CampaignSelect({ value, onChange }: Props) {
  const campaigns = useAllCampaigns();
  const known = campaigns.data?.some((campaign) => String(campaign.id) === value);

  return (
    <select
      aria-label="Campaign"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="input"
    >
      <option value="">All campaigns</option>
      {campaigns.data?.map((campaign) => (
        <option key={campaign.id} value={campaign.id}>
          {campaign.name}
        </option>
      ))}
      {value && campaigns.data && !known && <option value={value}>Campaign #{value}</option>}
    </select>
  );
}
