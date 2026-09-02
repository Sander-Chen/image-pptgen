import type { Config, ModelProfile } from '../types';

export type ImageDirectLane = 'banana' | 'gpt_image2';

type ImageDirectConfigLike = Pick<Config, 'name' | 'route_model_bindings'> & Partial<Pick<Config, 'type'>>;
type ImageDirectProfileLike = Pick<ModelProfile, 'id' | 'role' | 'name' | 'model'>;

export type ImageDirectModelOption<
  TConfig extends ImageDirectConfigLike = Config,
  TProfile extends ImageDirectProfileLike = ModelProfile,
> = {
  key: string;
  label: string;
  lane: ImageDirectLane;
  config: TConfig;
  profile: TProfile;
};

const imageDirectModelCatalog: Array<{ key: string; label: string; lane: ImageDirectLane; aliases: string[] }> = [
  { key: 'nano_banana_2', label: 'Nano Banana 2', lane: 'banana', aliases: ['nanobanana2'] },
  { key: 'nano_banana_pro', label: 'Nano Banana Pro', lane: 'banana', aliases: ['nanobananapro', 'nanobanana3t'] },
  { key: 'gpt_image2', label: 'GPT image2', lane: 'gpt_image2', aliases: ['gptimage2'] },
];

function normalizedModelToken(value?: string | null): string {
  return String(value || '').toLowerCase().replace(/[^a-z0-9]/g, '');
}

function boundImageGeneratorProfileId(config: ImageDirectConfigLike): number | null {
  const binding = config.route_model_bindings?.image_generator;
  if (typeof binding === 'number') return binding;
  if (typeof binding === 'string' && binding.trim()) {
    const parsed = Number(binding);
    return Number.isFinite(parsed) ? parsed : null;
  }
  if (binding && typeof binding === 'object' && 'profile_id' in binding) {
    const parsed = Number((binding as { profile_id?: unknown }).profile_id);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function imageDirectCatalogMatch(config: ImageDirectConfigLike, profile?: ImageDirectProfileLike) {
  const candidates = [
    normalizedModelToken(config.name),
    normalizedModelToken(profile?.name),
    normalizedModelToken(profile?.model),
  ];
  return imageDirectModelCatalog.find((item) => item.aliases.some((alias) => candidates.includes(alias)));
}

export function buildImageDirectModelOptions<
  TConfig extends ImageDirectConfigLike,
  TProfile extends ImageDirectProfileLike,
>(configs: TConfig[], modelProfiles: TProfile[]): Array<ImageDirectModelOption<TConfig, TProfile>> {
  const seenCatalogKeys = new Set<string>();
  const options: Array<ImageDirectModelOption<TConfig, TProfile>> = [];

  for (const config of configs) {
    if ((config.type || 'html') !== 'image') continue;
    const profileId = boundImageGeneratorProfileId(config);
    const profile = modelProfiles.find((item) => item.id === profileId && item.role === 'image_generator');
    if (!profile) continue;
    const catalog = imageDirectCatalogMatch(config, profile);
    if (!catalog || seenCatalogKeys.has(catalog.key)) continue;
    seenCatalogKeys.add(catalog.key);
    options.push({
      key: catalog.key,
      label: catalog.label,
      lane: catalog.lane,
      config,
      profile,
    });
  }

  return options;
}
