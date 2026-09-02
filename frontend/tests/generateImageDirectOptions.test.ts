import test from 'node:test';
import assert from 'node:assert/strict';

import { buildImageDirectModelOptions } from '../src/pages/generateImageDirectOptions';

const modelProfiles = [
  { id: 101, role: 'image_generator' as const, name: 'Nano Banana 2', model: 'nanobanana2' },
  { id: 102, role: 'image_generator' as const, name: 'Nano Banana Pro', model: 'nanobananapro' },
  { id: 201, role: 'image_generator' as const, name: 'GPT image2', model: 'gptimage2' },
  { id: 202, role: 'image_generator' as const, name: 'GPT image2 duplicate profile', model: 'gptimage2' },
];

const configs = [
  { id: 20, name: 'Nano Banana 2', type: 'image' as const, timeout_minutes: 30, route_model_bindings: { image_generator: 101 } },
  { id: 21, name: 'Nano Banana Pro', type: 'image' as const, timeout_minutes: 30, route_model_bindings: { image_generator: 102 } },
  { id: 22, name: 'GPT image2', type: 'image' as const, timeout_minutes: 30, route_model_bindings: { image_generator: 201 } },
  { id: 23, name: 'GPT image2 duplicate config', type: 'image' as const, timeout_minutes: 30, route_model_bindings: { image_generator: 202 } },
  { id: 24, name: 'GPT image2 name-only duplicate', type: 'image' as const, timeout_minutes: 30, route_model_bindings: {} },
];

test('buildImageDirectModelOptions keeps one option per ImageDirect catalog model', () => {
  const options = buildImageDirectModelOptions(configs, modelProfiles);

  assert.deepEqual(options.map((option) => option.key), ['nano_banana_2', 'nano_banana_pro', 'gpt_image2']);
  assert.deepEqual(options.filter((option) => option.lane === 'gpt_image2').map((option) => option.label), ['GPT image2']);
});

test('existing ImageDirect model selections still resolve to their legacy Config bindings', () => {
  const options = buildImageDirectModelOptions(configs, modelProfiles);
  const selectedConfigIds = ['nano_banana_2', 'nano_banana_pro', 'gpt_image2'].map((key) => {
    const option = options.find((item) => item.key === key);
    assert.ok(option, `legacy ImageDirect option ${key} must remain selectable`);
    return option.config.id;
  });

  assert.deepEqual(selectedConfigIds, [20, 21, 22]);
});
