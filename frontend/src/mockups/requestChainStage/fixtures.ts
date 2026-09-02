export type StageHealth = 'complete' | 'failed' | 'skipped' | 'legacy_missing';

export interface StageEvidence {
  id: string;
  stageName: string;
  role: string;
  model: string;
  profile: string;
  configuredThinking: 'low' | 'medium' | 'high' | 'not_applicable';
  mappedProviderThinking: string;
  rawThinkingFields: Record<string, unknown>;
  health: StageHealth;
  promptPath?: string;
  requestPath?: string;
  responsePath?: string;
  artifactPath?: string;
  attemptCount?: number;
  references?: Array<{ label: string; value: string; sent: boolean }>;
  prompt: string;
  request: Record<string, unknown>;
  response: Record<string, unknown>;
}

export interface SlideEvidence {
  id: number;
  position: number;
  title: string;
  type: 'cover' | 'content';
  status: 'completed' | 'failed' | 'skipped';
  previewKind: 'html' | 'image';
  visualVariant: 'cover' | 'content' | 'html';
  previewTitle: string;
  previewBody: string;
  versionTag: string;
  requestChain: {
    schemaVersion: number;
    strategy: string;
    health: StageHealth;
    plannedChain: string[];
    actualEvidence: Record<string, string>;
    stages: StageEvidence[];
  };
}

export interface MockRun {
  key: string;
  title: string;
  engine: 'html' | 'image';
  strategy: string;
  runId: number;
  batchId: number;
  configName: string;
  status: 'completed';
  modelSummary: string;
  routeFlowSteps: string[];
  slides: SlideEvidence[];
  designPrincipleRaw?: string;
}

const designPrompt = `# Designer Agent
Deck: Real copied history deck fixture
Requirement: Keep evidence debuggable and route-specific
Color: System Empty Color

Return a design principle JSON that every slide generation stage must consume.`;

const htmlPrompt = `# HTML Agent
Deck-Design-principle: {"layout":"dense evidence review","contrast":"high"}
Deck-User-Requirement: Request Chain must expose model, thinking, and raw provider request.
Slide-Content: Evidence for slide 1, with model stages and raw request paths.`;

const blueprintPrompt = `# Image Designer
Deck-Full-Content: Existing copied deck content
Deck-User-Requirement: Generate a visual blueprint with audit-ready evidence.
Deck-Required-color: <palette source="copied-run" />
Slide-Content: Stage-based request chain for Image route.`;

const imagePrompt = `# Image Generator
Render the current slide from the XML blueprint and preserve the configured visual evidence contract.`;

function geminiRequest(model: string, prompt: string, thinkingBudget: number, images: number = 0) {
  const parts: Array<Record<string, unknown>> = [{ text: prompt }];
  for (let index = 0; index < images; index += 1) {
    parts.push({ inlineData: { mimeType: 'image/png', data: '[IMAGE_BYTES_REDACTED]' } });
  }
  return {
    url: `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`,
    headers: { 'Content-Type': 'application/json', 'x-goog-api-key': '[REDACTED]' },
    json: {
      contents: [{ role: 'user', parts }],
      generationConfig: {
        temperature: 1,
        thinkingConfig: { thinkingBudget },
      },
    },
  };
}

function openAiRequest(model: string, prompt: string, effort: string) {
  return {
    endpoint: 'https://api.openai-compatible.local/v1/chat/completions',
    headers: { Authorization: 'Bearer [REDACTED]' },
    json: {
      model,
      messages: [{ role: 'user', content: prompt }],
      temperature: 0.7,
      thinking: { type: 'enabled', effort },
    },
  };
}

function response(stage: string, content: string) {
  return {
    status_code: 200,
    elapsed_seconds: 8.42,
    stage,
    response_preview: content,
    attempts: [{ attempt: 1, status_code: 200, elapsed_seconds: 8.42, transient: false }],
  };
}

const sharedDesignStage: StageEvidence = {
  id: 'design-principle',
  stageName: 'Design Principle',
  role: 'designer',
  model: 'gemini-3.1-pro-preview',
  profile: 'Test / Designer',
  configuredThinking: 'high',
  mappedProviderThinking: 'gemini.generationConfig.thinkingConfig.thinkingBudget = high',
  rawThinkingFields: { 'generationConfig.thinkingConfig.thinkingBudget': 10000 },
  health: 'complete',
  promptPath: 'run/shared-stages/design-principle/rendered-prompt.txt',
  requestPath: 'run/shared-stages/design-principle/request.json',
  responsePath: 'run/shared-stages/design-principle/response.json',
  attemptCount: 1,
  prompt: designPrompt,
  request: geminiRequest('gemini-3.1-pro-preview', designPrompt, 10000),
  response: response('design-principle', '{"layout":"dense evidence review","contrast":"high"}'),
};

const htmlStage: StageEvidence = {
  id: 'html-generation',
  stageName: 'HTML Generation',
  role: 'html_agent',
  model: 'gemini-3.1-pro-preview',
  profile: 'Test / HTML Agent',
  configuredThinking: 'medium',
  mappedProviderThinking: 'gemini.generationConfig.thinkingConfig.thinkingBudget = medium',
  rawThinkingFields: { 'generationConfig.thinkingConfig.thinkingBudget': 4000 },
  health: 'complete',
  promptPath: 'slides/slide-01/stages/html-generation/rendered-prompt.txt',
  requestPath: 'slides/slide-01/stages/html-generation/request.json',
  responsePath: 'slides/slide-01/stages/html-generation/response.json',
  artifactPath: 'slides/slide-01/stages/html-generation/clean.html',
  attemptCount: 1,
  prompt: htmlPrompt,
  request: geminiRequest('gemini-3.1-pro-preview', htmlPrompt, 4000),
  response: response('html-generation', '<!doctype html><html><body>Generated slide</body></html>'),
};

const screenshotStage: StageEvidence = {
  id: 'screenshot-capture',
  stageName: 'Screenshot Capture',
  role: 'playwright',
  model: 'not_applicable',
  profile: 'runtime',
  configuredThinking: 'not_applicable',
  mappedProviderThinking: 'not_applicable',
  rawThinkingFields: {},
  health: 'complete',
  requestPath: 'slides/slide-01/stages/screenshot-capture/request.json',
  responsePath: 'slides/slide-01/stages/screenshot-capture/response.json',
  artifactPath: 'slides/slide-01/stages/screenshot-capture/slide.png',
  attemptCount: 1,
  prompt: 'No prompt. Browser capture reads the generated HTML artifact.',
  request: { viewport: { width: 1280, height: 720 }, source_html_path: 'slide-01.html' },
  response: { status: 'complete', screenshot_path: 'slide-01.png' },
};

function blueprintStage(strategy: string, thinkingBudget: number, effort: 'low' | 'medium' | 'high', images = 0): StageEvidence {
  return {
    id: 'blueprint-generation',
    stageName: strategy === 'image_1_0' ? 'Direct Image Prompt' : 'Blueprint Generation',
    role: strategy === 'image_1_0' ? 'image_generator' : 'image_designer',
    model: strategy === 'image_5_0' ? 'openai/gpt-5.4' : 'gemini-3.1-pro-preview',
    profile: strategy === 'image_5_0' ? 'Test / Image Designer OpenAI' : 'Test / Image Designer',
    configuredThinking: effort,
    mappedProviderThinking: strategy === 'image_5_0'
      ? `openai-compatible.thinking.effort = ${effort}`
      : `gemini.generationConfig.thinkingConfig.thinkingBudget = ${effort}`,
    rawThinkingFields: strategy === 'image_5_0'
      ? { 'thinking.type': 'enabled', 'thinking.effort': effort }
      : { 'generationConfig.thinkingConfig.thinkingBudget': thinkingBudget },
    health: 'complete',
    promptPath: `slides/slide-02/stages/${strategy}/blueprint/rendered-prompt.txt`,
    requestPath: `slides/slide-02/stages/${strategy}/blueprint/request.json`,
    responsePath: `slides/slide-02/stages/${strategy}/blueprint/response.json`,
    artifactPath: strategy === 'image_1_0' ? undefined : `slides/slide-02/stages/${strategy}/blueprint/slide.xml`,
    attemptCount: 1,
    references: images ? [{ label: 'seed_png', value: 'artifacts/copied-run/slide-02.png', sent: true }] : undefined,
    prompt: strategy === 'image_1_0' ? imagePrompt : blueprintPrompt,
    request: strategy === 'image_5_0'
      ? openAiRequest('openai/gpt-5.4', blueprintPrompt, effort)
      : geminiRequest('gemini-3.1-pro-preview', strategy === 'image_1_0' ? imagePrompt : blueprintPrompt, thinkingBudget, images),
    response: response('blueprint-generation', '<SlideBlueprint><Layout>Evidence grid</Layout></SlideBlueprint>'),
  };
}

function imageGenerationStage(strategy: string, thinkingBudget: number, effort: 'low' | 'medium' | 'high', images = 0): StageEvidence {
  return {
    id: 'image-generation',
    stageName: 'Image Generation',
    role: 'image_generator',
    model: 'gemini-3.1-flash-image',
    profile: 'Test / Image Generator',
    configuredThinking: effort,
    mappedProviderThinking: `gemini.generationConfig.thinkingConfig.thinkingBudget = ${effort}`,
    rawThinkingFields: {
      'generationConfig.thinkingConfig.thinkingBudget': thinkingBudget,
      'generationConfig.responseModalities': ['TEXT', 'IMAGE'],
    },
    health: 'complete',
    requestPath: `slides/slide-02/stages/${strategy}/image-generation/request.json`,
    responsePath: `slides/slide-02/stages/${strategy}/image-generation/response.json`,
    artifactPath: `slides/slide-02/stages/${strategy}/image-generation/slide.png`,
    attemptCount: 1,
    references: images ? [{ label: 'reference_png', value: 'artifacts/copied-run/reference.png', sent: true }] : undefined,
    prompt: imagePrompt,
    request: {
      ...geminiRequest('gemini-3.1-flash-image', imagePrompt, thinkingBudget, images),
      conversation: {
        mode: strategy === 'image_1_0' ? 'image_1_0_first_content_context' : 'stateless',
        history_turn_count: strategy === 'image_1_0' ? 2 : 0,
      },
    },
    response: {
      ...response('image-generation', '[IMAGE_BYTES_SAVED]'),
      thought_signature_count: strategy === 'image_1_0' ? 1 : 0,
      image_path: `artifacts/request-chain-stage/${strategy}/slide-02.png`,
    },
  };
}

function htmlRun(): MockRun {
  const slideOneChain = {
    schemaVersion: 2,
    strategy: 'html_default',
    health: 'complete' as const,
    plannedChain: ['Design Principle', 'HTML Generation', 'Screenshot Capture'],
    actualEvidence: {
      design_principle_request_path: sharedDesignStage.requestPath || '',
      html_request_path: htmlStage.requestPath || '',
      screenshot_path: screenshotStage.artifactPath || '',
    },
    stages: [sharedDesignStage, htmlStage, screenshotStage],
  };
  return {
    key: 'html',
    title: 'HTML Default / copied history fixture',
    engine: 'html',
    strategy: 'html_default',
    runId: 87,
    batchId: 41,
    configName: 'Test HTML Combination',
    status: 'completed',
    modelSummary: 'Design Principle + HTML Generation',
    routeFlowSteps: ['Design Principle', 'HTML Generation', 'Screenshot Capture'],
    designPrincipleRaw: '{"layout":"dense evidence review","contrast":"high"}',
    slides: [
      {
        id: 8701,
        position: 1,
        title: 'Evidence For Slide 1',
        type: 'content',
        status: 'completed',
        previewKind: 'html',
        visualVariant: 'html',
        previewTitle: 'HTML preview',
        previewBody: 'Generated HTML slide with evidence chain inspector.',
        versionTag: 'v1',
        requestChain: slideOneChain,
      },
      {
        id: 8702,
        position: 2,
        title: 'Evidence For Slide 2',
        type: 'content',
        status: 'completed',
        previewKind: 'html',
        visualVariant: 'html',
        previewTitle: 'HTML preview',
        previewBody: 'Second generated HTML slide with active request-chain evidence.',
        versionTag: 'v2',
        requestChain: slideOneChain,
      },
    ],
  };
}

function imageRun(strategy: 'image_1_0' | 'image_3_0' | 'image_3_2' | 'image_5_0'): MockRun {
  const referenceCount = strategy === 'image_3_0' || strategy === 'image_3_2' ? 1 : 0;
  const thinking: Record<typeof strategy, ['low' | 'medium' | 'high', number]> = {
    image_1_0: ['low', 1024],
    image_3_0: ['medium', 4000],
    image_3_2: ['medium', 4000],
    image_5_0: ['high', 10000],
  };
  const [effort, budget] = thinking[strategy];
  const coverStage = imageGenerationStage(strategy, 1024, 'low', 0);
  coverStage.id = 'cover-image-generation';
  coverStage.stageName = 'Cover Image Generation';
  coverStage.requestPath = `slides/slide-01/stages/${strategy}/cover-image/request.json`;
  coverStage.responsePath = `slides/slide-01/stages/${strategy}/cover-image/response.json`;
  const stages = [
    coverStage,
    blueprintStage(strategy, budget, effort, referenceCount),
    imageGenerationStage(strategy, budget, effort, referenceCount),
  ];
  const routeFlowByStrategy = {
    image_1_0: ['Cover Image Generation', 'Direct Image Prompt', 'Image request'],
    image_3_0: ['Seed slide', 'Blueprint XML', 'Image request'],
    image_3_2: ['Cover palette reference', 'Blueprint XML', 'Image request'],
    image_5_0: ['Unified Designer', 'Blueprint XML', 'Image request'],
  };
  const coverChain = {
    schemaVersion: 2,
    strategy,
    health: 'complete' as const,
    plannedChain: ['Cover Image Generation'],
    actualEvidence: {
      cover_image_request_path: coverStage.requestPath || '',
      cover_image_response_path: coverStage.responsePath || '',
    },
    stages: [coverStage],
  };
  return {
    key: strategy,
    title: `${strategy.replace('image_', 'Image ')} / copied history fixture`,
    engine: 'image',
    strategy,
    runId: 120 + Number(strategy.slice(-1).replace('0', '5')),
    batchId: 52,
    configName: `Test Image Combination (${strategy})`,
    status: 'completed',
    modelSummary: strategy === 'image_1_0' ? 'Direct prompt + image generation' : 'Blueprint + image generation',
    routeFlowSteps: routeFlowByStrategy[strategy],
    slides: [
      {
        id: 9000,
        position: 1,
        title: '中国历史1',
        type: 'cover',
        status: 'completed',
        previewKind: 'image',
        visualVariant: 'cover',
        previewTitle: '中国历史',
        previewBody: '源远流长，生生不息',
        versionTag: 'v1',
        requestChain: coverChain,
      },
      {
        id: 9001,
        position: 2,
        title: '中国历史2',
        type: 'content',
        status: 'completed',
        previewKind: 'image',
        visualVariant: 'content',
        previewTitle: 'Image preview',
        previewBody: 'Stage evidence verifies prompt, request body, references, and thinking fields.',
        versionTag: 'v2',
        requestChain: {
          schemaVersion: 2,
          strategy,
          health: 'complete',
          plannedChain: stages.map((stage) => stage.stageName),
          actualEvidence: {
            request_chain_json: `slides/slide-02/${strategy}/request-chain.json`,
            image_request_path: stages[stages.length - 1].requestPath || '',
            image_response_path: stages[stages.length - 1].responsePath || '',
          },
          stages,
        },
      },
    ],
  };
}

export const mockRuns: MockRun[] = [
  htmlRun(),
  imageRun('image_1_0'),
  imageRun('image_3_0'),
  imageRun('image_3_2'),
  imageRun('image_5_0'),
];
