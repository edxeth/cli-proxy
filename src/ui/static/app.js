// Vue 3 + Element Plus CLI Proxy Monitor Application
const { createApp, ref, reactive, computed, onMounted, onBeforeUnmount, nextTick, watch } = Vue;
const { ElMessage, ElMessageBox } = ElementPlus;

const app = createApp({
    setup() {
        const SERVICE_NAMES = ['claude', 'legacy', 'codex'];
        // Reactive state
        const loading = ref(false);
        const logsLoading = ref(false);
        const allLogsLoading = ref(false);
        const configSaving = ref(false);
        const filterSaving = ref(false);
        const lastUpdate = ref('Loading...');
        
        // Service status state
        const services = reactive({
            claude: {
                running: false,
                pid: null,
                config: ''
            },
            legacy: {
                running: false,
                pid: null,
                config: ''
            },
            codex: {
                running: false,
                pid: null,
                config: ''
            }
        });
        
        // Statistics
        const stats = reactive({
            requestCount: 0,
            configCount: 0,
            filterCount: 0
        });
        
        // Log collections
        const logs = ref([]);
        const allLogs = ref([]);
        
        // Configuration options
        const claudeConfigs = ref([]);
        const legacyConfigs = ref([]);
        const codexConfigs = ref([]);
        const configMetadata = reactive({
            claude: {},
            legacy: {},
            codex: {}
        });
        
        // Drawer visibility state
        const configDrawerVisible = ref(false);
        const filterDrawerVisible = ref(false);
        const logDetailVisible = ref(false);
        const allLogsVisible = ref(false);
        const activeConfigTab = ref('claude');
        const activeLogTab = ref('basic'); // Current log detail tab
        const showTransformedData = ref(true); // Whether to show transformed payloads by default
        const activeRequestSubTab = ref('headers'); // Request drawer sub-tab: 'headers' | 'body'
        const activeResponseSubTab = ref('headers'); // Response drawer sub-tab: 'headers' | 'body'

        // Configuration content
        const configContents = reactive({
            claude: '',
            legacy: '',
            codex: ''
        });
        const filterContent = ref('');
        const filterRules = ref([]);  // Filter rule list

        // Friendly form configuration data
        const friendlyConfigs = reactive({
            claude: [],  // [{ name, baseUrl, authType, authValue, active }]
            legacy: [],
            codex: []
        });

        // Configuration edit mode 'interactive' | 'json' | 'merged'
        const configEditMode = ref('merged');

        // Data used by merged configuration mode
        const mergedConfigs = reactive({
            claude: [],  // Each entry is a group
            legacy: [],
            codex: []
        });

        // Add-site dialog state
        const mergedDialogVisible = ref(false);
        const mergedDialogService = ref('');
        const mergedDialogDraft = reactive({
            baseUrl: 'https://',
            weight: 0,
            authType: 'auth_token',
            rpmLimit: null,
            streaming: null,  // null (auto), true (force on), false (force off)
            entries: []  // [{ name, authValue, active }]
        });
        const mergedDialogMode = ref('add');
        const mergedDialogEditIndex = ref(-1);

        // New site edit state
        const editingNewSite = reactive({
            claude: false,
            legacy: false,
            codex: false
        });

        // New site form data
        const newSiteData = reactive({
            claude: {
                name: '',
                baseUrl: 'https://',
                authType: 'auth_token',
                authValue: '',
                active: false,
                weight: 0
            },
            legacy: {
                name: '',
                baseUrl: 'https://',
                authType: 'auth_token',
                authValue: '',
                active: false,
                weight: 0,
                rpmLimit: 0,
                streaming: null
            },
            codex: {
                name: '',
                baseUrl: 'https://',
                authType: 'auth_token',
                authValue: '',
                active: false,
                weight: 0
            }
        });

        // Test utility state
        const modelSelectorVisible = ref(false);
        const testResultVisible = ref(false);
        const testingConnection = ref(false);
        const testConfig = reactive({
            service: '',
            siteData: null,
            isNewSite: false,
            siteIndex: -1,
            model: '',
            reasoningEffort: '',
            reasoningSummary: 'auto'
        });
        const lastTestResult = reactive({
            success: false,
            status_code: null,
            response_text: '',
            target_url: '',
            error_message: null
        });

        // Result of testing newly added sites
        const newSiteTestResult = reactive({
            claude: null,
            legacy: null,
            codex: null
        });

        // Test response dialog
        const testResponseDialogVisible = ref(false);
        const testResponseData = ref('');

        // Synchronisation guards to avoid loops
        const syncInProgress = ref(false);
        const selectedLog = ref(null);
        const decodedRequestBody = ref(''); // Decoded request body (transformed)
        const decodedOriginalRequestBody = ref(''); // Decoded original request body
        const decodedResponseContent = ref(''); // Decoded response content
        const responseOriginalContent = ref(''); // Raw response content
        const parsedResponseContent = ref(''); // Parsed summary response content
        const isResponseContentParsed = ref(true); // Whether the parsed view is showing
        const isCodexLog = ref(false); // Selected log comes from codex
        const isClaudeLog = ref(false); // Selected log comes from claude

        // Codex model settings (default reasoning effort and verbosity)
        const modelSettingsVisible = ref(false);
        const effortByModel = reactive({ 'gpt-5': 'medium', 'gpt-5-codex': 'medium' });
        const verbosityByModel = reactive({ 'gpt-5': 'medium', 'gpt-5-codex': 'medium' });
        const summaryByModel = reactive({ 'gpt-5': 'auto', 'gpt-5-codex': 'auto' });

        const openModelSettings = async () => {
            try {
                const res = await fetch('/api/codex/settings');
                const data = await res.json();
                const eff = data.effortByModel || {};
                const verb = data.verbosityByModel || {};
                const summ = data.summaryByModel || {};
                effortByModel['gpt-5'] = eff['gpt-5'] || 'medium';
                effortByModel['gpt-5-codex'] = eff['gpt-5-codex'] || 'medium';
                verbosityByModel['gpt-5'] = verb['gpt-5'] || 'medium';
                verbosityByModel['gpt-5-codex'] = verb['gpt-5-codex'] || 'medium';
                summaryByModel['gpt-5'] = summ['gpt-5'] || 'auto';
                summaryByModel['gpt-5-codex'] = summ['gpt-5-codex'] || 'auto';
            } catch (error) {
                console.warn('Failed to load model settings:', error);
            }
            modelSettingsVisible.value = true;
        };

        const saveModelSettings = async () => {
            try {
                const response = await fetch('/api/codex/settings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ effortByModel, verbosityByModel, summaryByModel })
                });
                const result = await response.json();
                if (response.ok && result.success) {
                    ElMessage.success('Model settings saved');
                    modelSettingsVisible.value = false;
                } else {
                    throw new Error(result.error || response.statusText);
                }
            } catch (error) {
                ElMessage.error('Failed to save model settings: ' + error.message);
            }
        };
        const canParseSelectedLog = computed(() => {
            return (isCodexLog.value || isClaudeLog.value) && !!responseOriginalContent.value;
        });

        // Real-time request state
        const realtimeRequests = ref([]);
        const realtimeDetailVisible = ref(false);
        const selectedRealtimeRequest = ref(null);
        const connectionStatus = reactive({ claude: false, codex: false, legacy: false });
        const realtimeManager = ref(null);
        const maxRealtimeRequests = 20;

        // Model routing state
        const routingMode = ref('default'); // 'default' | 'model-mapping' | 'config-mapping'
        const modelMappingDrawerVisible = ref(false);
        const configMappingDrawerVisible = ref(false);
        const activeModelMappingTab = ref('claude'); // Default to claude tab
        const activeConfigMappingTab = ref('claude'); // Default to claude tab
        const routingConfig = reactive({
            mode: 'default',
            modelMappings: {
                claude: [],  // [{ source: 'sonnet4', target: 'opus4' }]
                codex: [],
                legacy: []
            },
            configMappings: {
                claude: [],  // [{ model: 'sonnet4', config: 'config_a' }]
                codex: [],
                legacy: []
            }
        });
        const routingConfigSaving = ref(false);

        // Load balancing state
        const loadbalanceConfig = reactive({
            mode: 'active-first',
            services: {
                claude: {
                    failureThreshold: 3,
                    currentFailures: {},
                    excludedConfigs: []
                },
                legacy: {
                    failureThreshold: 3,
                    currentFailures: {},
                    excludedConfigs: []
                },
                codex: {
                    failureThreshold: 3,
                    currentFailures: {},
                    excludedConfigs: []
                }
            }
        });
        const loadbalanceSaving = ref(false);
        const loadbalanceLoading = ref(false);
        const resettingFailures = reactive({ claude: false, codex: false, legacy: false });
        const isLoadbalanceWeightMode = computed(() => loadbalanceConfig.mode === 'weight-based');
        const loadbalanceDisabledNotice = computed(() => isLoadbalanceWeightMode.value ? 'Load balancing in effect' : '');
        const PINNED_WEIGHT_START = 1000;
        const pinningState = reactive({
            claude: { loading: false, target: '' },
            codex: { loading: false, target: '' },
            legacy: { loading: false, target: '' }
        });

        // Responsive layout controls
        const defaultViewport = {
            width: typeof window !== 'undefined' ? window.innerWidth : 1440,
            height: typeof window !== 'undefined' ? window.innerHeight : 900
        };
        const viewport = reactive({ ...defaultViewport });

        const updateViewport = () => {
            if (typeof window === 'undefined') {
                return;
            }
            viewport.width = window.innerWidth;
            viewport.height = window.innerHeight;
        };

        const getDrawerSize = (desktop, tablet = '82%', mobile = '100%') => {
            const width = viewport.width;
            if (width <= 640) {
                return mobile;
            }
            if (width <= 1280) {
                return tablet;
            }
            return desktop;
        };

        const drawerSizes = computed(() => ({
            config: getDrawerSize('600px', '82%', '100%'),
            filter: getDrawerSize('700px', '85%', '100%'),
            logDetail: getDrawerSize('900px', '88%', '100%'),
            allLogs: getDrawerSize('50%', '82%', '100%'),
            usage: getDrawerSize('1000px', '88%', '100%'),
            realtime: getDrawerSize('800px', '85%', '100%'),
            modelMapping: getDrawerSize('60%', '82%', '100%'),
            configMapping: getDrawerSize('60%', '82%', '100%'),
            mapping: getDrawerSize('720px', '90%', '100%')
        }));

        const dialogSizes = computed(() => ({
            modelSelector: getDrawerSize('400px', '70%', '96%'),
            mergedGroup: getDrawerSize('700px', '90%', '96%')
        }));

        const isMobileViewport = computed(() => viewport.width <= 640);

        // System configuration
        const systemConfig = reactive({
            logLimit: 50
        });

        // Request status mapping
        const REQUEST_STATUS = {
            PENDING: { text: 'Requested', type: 'warning' },
            STREAMING: { text: 'Streaming', type: 'primary' },
            COMPLETED: { text: 'Completed', type: 'success' },
            FAILED: { text: 'Failed', type: 'danger' }
        };

        const metricKeys = ['input', 'cached_create', 'cached_read', 'output', 'reasoning', 'total'];
        const createEmptyMetrics = () => ({
            input: 0,
            cached_create: 0,
            cached_read: 0,
            output: 0,
            reasoning: 0,
            total: 0
        });
        const createEmptyFormatted = () => {
            const formatted = {};
            metricKeys.forEach(key => {
                formatted[key] = '0';
            });
            return formatted;
        };

        const usageSummary = reactive({
            totals: createEmptyMetrics(),
            formattedTotals: createEmptyFormatted(),
            perService: {}
        });
        const usageDrawerVisible = ref(false);
        const usageDetailsLoading = ref(false);
        const usageDetails = reactive({
            totals: {
                metrics: createEmptyMetrics(),
                formatted: createEmptyFormatted()
            },
            services: {}
        });
        const usageMetricLabels = {
            input: 'Input',
            cached_create: 'Cache Create',
            cached_read: 'Cache Read',
            output: 'Output',
            reasoning: 'Reasoning',
            total: 'Total'
        };
        
        const normalizeUsageBlock = (block) => {
            const isMetricsMap = block && typeof block === 'object' && !Array.isArray(block) && metricKeys.some(key => key in block);
            const metricsSource = isMetricsMap ? block : (block?.metrics || {});
            const formattedSource = block?.formatted || {};
            const displayMetricsSource = block?.displayMetrics || metricsSource;
            const displayFormattedSource = block?.displayFormatted || formattedSource;

            return {
                metrics: Object.assign(createEmptyMetrics(), metricsSource || {}),
                formatted: Object.assign(createEmptyFormatted(), formattedSource || {}),
                displayMetrics: Object.assign(createEmptyMetrics(), displayMetricsSource || {}),
                displayFormatted: Object.assign(createEmptyFormatted(), displayFormattedSource || {}),
            };
        };

        const resetUsageSummary = () => {
            usageSummary.totals = createEmptyMetrics();
            usageSummary.formattedTotals = createEmptyFormatted();
            usageSummary.perService = {};
        };

        const resetUsageDetails = () => {
            usageDetails.totals = normalizeUsageBlock({});
            usageDetails.services = {};
        };

        const formatUsageValue = (value) => {
            const num = Number(value || 0);
            if (!Number.isFinite(num)) {
                return '-';
            }
            const intVal = Math.trunc(num);
            if (intVal >= 1_000_000) {
                const short = Math.floor(intVal / 100_000) / 10;
                return `${intVal} (${short.toFixed(1)}m)`;
            }
            if (intVal >= 1_000) {
                const short = Math.floor(intVal / 100) / 10;
                return `${intVal} (${short.toFixed(1)}k)`;
            }
            return `${intVal}`;
        };

        const getNumeric = (value) => {
            const num = Number(value || 0);
            return Number.isFinite(num) ? num : 0;
        };

        const updateFormattedFromMetrics = (block) => {
            if (!block) {
                return block;
            }
            if (!block.metrics) {
                block.metrics = createEmptyMetrics();
            }
            if (!block.displayMetrics) {
                block.displayMetrics = Object.assign(createEmptyMetrics(), block.metrics);
            }
            if (!block.displayFormatted) {
                block.displayFormatted = createEmptyFormatted();
            }
            metricKeys.forEach(key => {
                block.displayFormatted[key] = formatUsageValue(getNumeric(block.displayMetrics?.[key]));
            });
            block.formatted = block.displayFormatted;
            return block;
        };

        const adjustUsageBlockForService = (service, block) => {
            const normalized = normalizeUsageBlock(block);
            if (!normalized.metrics) {
                return normalized;
            }
            if (service === 'codex') {
                const cachedRead = getNumeric(normalized.metrics.cached_read);
                const adjustedInput = Math.max(0, getNumeric(normalized.metrics.input) - cachedRead);
                const adjustedTotal = Math.max(0, getNumeric(normalized.metrics.total) - cachedRead);
                normalized.displayMetrics.input = adjustedInput;
                normalized.displayMetrics.total = adjustedTotal;
                normalized.displayMetrics.cached_read = getNumeric(normalized.metrics.cached_read);
            } else {
                normalized.displayMetrics = Object.assign(createEmptyMetrics(), normalized.metrics);
            }
            return updateFormattedFromMetrics(normalized);
        };

        const mergeMetricsInto = (target, sourceMetrics) => {
            if (!sourceMetrics) {
                return;
            }
            metricKeys.forEach(key => {
                target[key] = getNumeric(target[key]) + getNumeric(sourceMetrics?.[key]);
            });
        };

        const formatUsageSummary = (usage, serviceOverride = null) => {
            if (!usage || !usage.metrics) {
                return '-';
            }
            const metrics = usage.metrics;
            const service = serviceOverride || usage.service || '';
            const cachedRead = getNumeric(metrics.cached_read);
            const displayInput = service === 'codex'
                ? Math.max(0, getNumeric(metrics.input) - cachedRead)
                : getNumeric(metrics.input);
            const displayTotal = service === 'codex'
                ? Math.max(0, getNumeric(metrics.total) - cachedRead)
                : getNumeric(metrics.total);
            const displayOutput = getNumeric(metrics.output);

            return [
                `IN ${formatUsageValue(displayInput)}`,
                `OUT ${formatUsageValue(displayOutput)}`,
                `Total ${formatUsageValue(displayTotal)}`
            ].join('\n');
        };

        const getUsageFormattedValue = (block, key) => {
            if (!block) return '-';
            const formattedBlock = block.displayFormatted || block.formatted;
            if (formattedBlock && formattedBlock[key]) {
                return formattedBlock[key];
            }
            const metricsSource = block.displayMetrics || block.metrics;
            if (metricsSource) {
                return formatUsageValue(metricsSource[key]);
            }
            return '-';
        };

        const formatChannelName = (name) => {
            if (!name) return 'Unknown';
            return name === 'unknown' ? 'Unlabeled' : name;
        };

        // Retrieve model options
        const getModelOptions = (service) => {
            if (service === 'claude') {
                return [
                    { label: 'claude-sonnet-4-5-20250929', value: 'claude-sonnet-4-5-20250929' },
                    { label: 'claude-sonnet-4-20250514', value: 'claude-sonnet-4-20250514' },
                    { label: 'claude-opus-4-20250514', value: 'claude-opus-4-20250514' },
                    { label: 'claude-opus-4-1-20250805', value: 'claude-opus-4-1-20250805' }
                ];
            } else if (service === 'codex') {
                return [
                    { label: 'gpt-5-codex', value: 'gpt-5-codex' },
                    { label: 'gpt-5', value: 'gpt-5' }
                ];
            } else if (service === 'legacy') {
                return [
                    { label: 'provider-2/gpt-5', value: 'provider-2/gpt-5' },
                    { label: 'provider-7/claude-sonnet-4-5-20250929', value: 'provider-7/claude-sonnet-4-5-20250929' },
                    { label: 'provider-7/claude-opus-4-1-20250805', value: 'provider-7/claude-opus-4-1-20250805' }
                ];
            }
            return [];
        };

        // Test connectivity for a newly added site
        const testNewSiteConnection = (service) => {
            const siteData = newSiteData[service];
            if (!siteData.name || !siteData.baseUrl || !siteData.authValue) {
                ElMessage.warning('Please complete all site details first');
                return;
            }
            showModelSelector(service, siteData, true);
        };

        // Test connectivity for an existing site
        const testSiteConnection = (service, siteIndex) => {
            const siteData = friendlyConfigs[service][siteIndex];
            if (!siteData.name || !siteData.baseUrl || !siteData.authValue) {
                ElMessage.warning('Site information is incomplete');
                return;
            }
            showModelSelector(service, siteData, false, siteIndex);
        };

        // Open the model selector dialog
        const showModelSelector = (service, siteData, isNewSite = false, siteIndex = -1) => {
            testConfig.service = service;
            testConfig.siteData = siteData;
            testConfig.isNewSite = isNewSite;
            testConfig.siteIndex = siteIndex;
            testConfig.model = '';

            // Reset previous test outcome
            Object.assign(lastTestResult, {
                success: false,
                status_code: null,
                response_text: '',
                target_url: '',
                error_message: null
            });

            // Select the default model
            const options = getModelOptions(service);
            if (options.length > 0) {
                testConfig.model = options[0].value;
            }

            // Set default reasoning effort
            if (service === 'codex') {
                testConfig.reasoningEffort = 'medium';
                testConfig.reasoningSummary = 'auto';
            } else {
                testConfig.reasoningEffort = '';
                testConfig.reasoningSummary = 'auto';
            }

            modelSelectorVisible.value = true;
        };

        // Cancel model selection
        const cancelModelSelection = () => {
            modelSelectorVisible.value = false;
            testConfig.service = '';
            testConfig.siteData = null;
            testConfig.isNewSite = false;
            testConfig.siteIndex = -1;
            testConfig.model = '';
            testConfig.reasoningEffort = '';
            testConfig.reasoningSummary = 'auto';
        };

        // Confirm selection and execute the connectivity test
        const confirmModelSelection = async () => {
            if (!testConfig.model) {
                ElMessage.warning('Please select a model to test');
                return;
            }

            // Reset stored test result
            Object.assign(lastTestResult, {
                success: false,
                status_code: null,
                response_text: '',
                target_url: '',
                error_message: null
            });

            testingConnection.value = true;
            // Keep the dialog open so results can be shown inline

            try {
                const siteData = testConfig.siteData;
                const requestData = {
                    service: testConfig.service,
                    model: testConfig.model,
                    base_url: siteData.baseUrl
                };

                // Populate authentication fields based on type
                if (siteData.authType === 'auth_token') {
                    requestData.auth_token = siteData.authValue;
                } else {
                    requestData.api_key = siteData.authValue;
                }

                // For codex include optional reasoning parameters
                if (testConfig.service === 'codex') {
                    const extraParams = {};
                    if (testConfig.reasoningEffort) {
                        extraParams.reasoning_effort = testConfig.reasoningEffort;
                    }
                    if (testConfig.reasoningSummary && testConfig.reasoningSummary !== 'off') {
                        extraParams.reasoning_summary = testConfig.reasoningSummary;
                    }
                    if (Object.keys(extraParams).length > 0) {
                        requestData.extra_params = extraParams;
                    }
                }

                const result = await fetchWithErrorHandling('/api/test-connection', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify(requestData)
                });

                // Store the result for the dialog to present
                Object.assign(lastTestResult, result);

                // Persist the outcome in the appropriate cache
                if (testConfig.isNewSite) {
                    // Newly added site result
                    newSiteTestResult[testConfig.service] = { ...result };
                } else {
                    // Existing site result
                    if (friendlyConfigs[testConfig.service] && friendlyConfigs[testConfig.service][testConfig.siteIndex]) {
                        friendlyConfigs[testConfig.service][testConfig.siteIndex].testResult = { ...result };
                    }
                }

                // No toast message; dialog already shows the outcome

            } catch (error) {
                const errorResult = {
                    success: false,
                    status_code: null,
                    response_text: error.message,
                    target_url: '',
                    error_message: error.message
                };

                // Save the error result for display
                Object.assign(lastTestResult, errorResult);

                // Reflect the failure in the cached record
                if (testConfig.isNewSite) {
                    newSiteTestResult[testConfig.service] = { ...errorResult };
                } else {
                    if (friendlyConfigs[testConfig.service] && friendlyConfigs[testConfig.service][testConfig.siteIndex]) {
                        friendlyConfigs[testConfig.service][testConfig.siteIndex].testResult = { ...errorResult };
                    }
                }
            } finally {
                testingConnection.value = false;
            }
        };

        // Copy test result text
        const copyTestResult = async () => {
            try {
                await copyToClipboard(lastTestResult.response_text);
            } catch (error) {
                ElMessage.error('Copy failed');
            }
        };

        // Show test response payload
        const showTestResponse = (type, service, index = null) => {
            let responseText = '';
            if (type === 'newSite') {
                responseText = newSiteTestResult[service]?.response_text || '';
            } else if (type === 'site' && index !== null) {
                responseText = friendlyConfigs[service][index]?.testResult?.response_text || '';
            }

            if (responseText) {
                testResponseData.value = responseText;
                testResponseDialogVisible.value = true;
            } else {
                ElMessage.warning('No response data available');
            }
        };

        // Copy the response payload from the dialog
        const copyTestResponseData = async () => {
            try {
                await copyToClipboard(testResponseData.value);
            } catch (error) {
                ElMessage.error('Copy failed');
            }
        };

        // Format service and channel display with line breaks
        const formatServiceWithChannel = (service, channel) => {
            const serviceName = service || '-';
            if (!channel || channel === 'unknown') {
                return serviceName;
            }
            return `${serviceName}\n[${channel}]`;
        };

        // Format method and URL pair
        const formatMethodWithURL = (method, url) => {
            const methodName = method || 'GET';
            const urlPath = url || '-';
            return `[${methodName}] ${urlPath}`;
        };

        const loadUsageDetails = async () => {
            usageDetailsLoading.value = true;
            try {
                const data = await fetchWithErrorHandling('/api/usage/details');
                const services = {};
                const serviceEntries = Object.entries(data.services || {});
                serviceEntries.forEach(([service, payload]) => {
                    const overallBlock = adjustUsageBlockForService(service, payload?.overall || {});
                    const channels = {};
                    Object.entries(payload?.channels || {}).forEach(([channel, channelPayload]) => {
                        if (!channel || channel === 'unknown') {
                            return;
                        }
                        channels[channel] = adjustUsageBlockForService(service, channelPayload || {});
                    });
                    services[service] = {
                        overall: overallBlock,
                        channels
                    };
                });
                usageDetails.services = services;

                if (serviceEntries.length === 0) {
                    usageDetails.totals = adjustUsageBlockForService('codex', data.totals || {});
                } else {
                    const totalMetrics = createEmptyMetrics();
                    serviceEntries.forEach(([service]) => {
                        mergeMetricsInto(totalMetrics, services[service]?.overall?.displayMetrics || services[service]?.overall?.metrics);
                    });
                    usageDetails.totals = updateFormattedFromMetrics({
                        metrics: Object.assign(createEmptyMetrics(), totalMetrics),
                        displayMetrics: Object.assign(createEmptyMetrics(), totalMetrics),
                        formatted: createEmptyFormatted()
                    });
                }
            } catch (error) {
                resetUsageDetails();
                ElMessage.error('Failed to fetch usage details: ' + error.message);
            } finally {
                usageDetailsLoading.value = false;
            }
        };

        const openUsageDrawer = async () => {
            usageDrawerVisible.value = true;
            await loadUsageDetails();
        };

        const closeUsageDrawer = () => {
            usageDrawerVisible.value = false;
        };

        // Clear token usage data
        const clearUsageData = async () => {
            try {
                await ElMessageBox.confirm(
                    'Clear all token usage records? This will wipe every log and reset token metrics. This cannot be undone.',
                    'Confirm Token Reset',
                    {
                        confirmButtonText: 'Confirm',
                        cancelButtonText: 'Cancel',
                        type: 'warning',
                    }
                );

                const result = await fetchWithErrorHandling('/api/usage/clear', {
                    method: 'DELETE'
                });

                if (result.success) {
                    ElMessage.success('Token usage records cleared');
                    // Refresh the page data
                    window.location.reload();
                } else {
                    ElMessage.error('Failed to clear token usage: ' + (result.error || 'Unknown error'));
                }
            } catch (error) {
                if (error !== 'cancel') {
                    ElMessage.error('Failed to clear token usage: ' + error.message);
                }
            }
        };

        // Model routing helpers
        const selectRoutingMode = async (mode) => {
            routingMode.value = mode;
            routingConfig.mode = mode;
            await saveRoutingConfig();
            ElMessage.success(`Switched to ${getRoutingModeText(mode)} mode`);
        };

        const getRoutingModeText = (mode) => {
            const modeTexts = {
                'default': 'Default routing',
                'model-mapping': 'Model→Model mapping',
                'config-mapping': 'Model→Config mapping'
            };
            return modeTexts[mode] || mode;
        };

        const openModelMappingDrawer = () => {
            modelMappingDrawerVisible.value = true;
        };

        const openConfigMappingDrawer = () => {
            configMappingDrawerVisible.value = true;
        };

        const closeModelMappingDrawer = () => {
            modelMappingDrawerVisible.value = false;
        };

        const closeConfigMappingDrawer = () => {
            configMappingDrawerVisible.value = false;
        };

        const addModelMapping = (service) => {
            routingConfig.modelMappings[service].push({
                source: '',
                target: '',
                source_type: 'model'
            });
        };

        const removeModelMapping = (service, index) => {
            routingConfig.modelMappings[service].splice(index, 1);
        };

        const addConfigMapping = (service) => {
            routingConfig.configMappings[service].push({
                model: '',
                config: ''
            });
        };

        const removeConfigMapping = (service, index) => {
            routingConfig.configMappings[service].splice(index, 1);
        };

        const saveRoutingConfig = async () => {
            routingConfigSaving.value = true;
            try {
                const result = await fetchWithErrorHandling('/api/routing/config', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify(routingConfig)
                });

                if (result.success) {
                    ElMessage.success('Routing configuration saved');
                } else {
                    ElMessage.error('Failed to save routing configuration: ' + (result.error || 'Unknown error'));
                }
            } catch (error) {
                ElMessage.error('Failed to save routing configuration: ' + error.message);
            } finally {
                routingConfigSaving.value = false;
            }
        };

        const loadRoutingConfig = async () => {
            try {
                const data = await fetchWithErrorHandling('/api/routing/config');
                if (data.config) {
                    Object.assign(routingConfig, data.config);
                    routingMode.value = data.config.mode || 'default';

                    // Backwards compatibility: ensure mappings without source_type default to model
                    SERVICE_NAMES.forEach(service => {
                        if (routingConfig.modelMappings[service]) {
                            routingConfig.modelMappings[service].forEach(mapping => {
                                if (!mapping.source_type) {
                                    mapping.source_type = 'model';
                                }
                            });
                        }
                    });
                }
            } catch (error) {
                console.error('Failed to load routing configuration:', error);
                // Fall back to default configuration
                routingMode.value = 'default';
                routingConfig.mode = 'default';
            }
        };

        const getLoadbalanceModeText = (mode) => {
            const mapping = {
                'active-first': 'Active-first',
                'weight-based': 'Weight-based'
            };
            return mapping[mode] || mode;
        };

        const normalizeLoadbalanceConfig = (payload = {}) => {
            const normalized = {
                mode: payload.mode === 'weight-based' ? 'weight-based' : 'active-first',
                services: {
                    claude: {
                        failureThreshold: 3,
                        currentFailures: {},
                        excludedConfigs: []
                    },
                    legacy: {
                        failureThreshold: 3,
                        currentFailures: {},
                        excludedConfigs: []
                    },
                    codex: {
                        failureThreshold: 3,
                        currentFailures: {},
                        excludedConfigs: []
                    }
                }
            };

            SERVICE_NAMES.forEach(service => {
                const section = payload.services?.[service] || {};
                const threshold = Number(section.failureThreshold ?? section.failover_count ?? 3);
                normalized.services[service].failureThreshold = Number.isFinite(threshold) && threshold > 0 ? threshold : 3;

                const rawFailures = section.currentFailures || section.current_failures || {};
                const normalizedFailures = {};
                Object.entries(rawFailures || {}).forEach(([name, count]) => {
                    const numeric = Number(count);
                    normalizedFailures[name] = Number.isFinite(numeric) && numeric > 0 ? numeric : 0;
                });
                normalized.services[service].currentFailures = normalizedFailures;

                const excludedList = section.excludedConfigs || section.excluded_configs || [];
                normalized.services[service].excludedConfigs = Array.isArray(excludedList) ? [...excludedList] : [];
            });

            return normalized;
        };

        const applyLoadbalanceConfig = (normalized) => {
            loadbalanceConfig.mode = normalized.mode;
            SERVICE_NAMES.forEach(service => {
                const svc = normalized.services[service];
                loadbalanceConfig.services[service].failureThreshold = svc.failureThreshold;
                loadbalanceConfig.services[service].currentFailures = Object.assign({}, svc.currentFailures);
                loadbalanceConfig.services[service].excludedConfigs = [...svc.excludedConfigs];
            });
        };

        const buildLoadbalancePayload = () => {
            const buildServiceSection = (service) => {
                const section = loadbalanceConfig.services[service] || {};
                const threshold = Number(section.failureThreshold ?? 3);
                const normalizedThreshold = Number.isFinite(threshold) && threshold > 0 ? threshold : 3;
                const failuresPayload = {};
                Object.entries(section.currentFailures || {}).forEach(([name, count]) => {
                    const numeric = Number(count);
                    failuresPayload[name] = Number.isFinite(numeric) && numeric > 0 ? numeric : 0;
                });
                const excludedPayload = Array.isArray(section.excludedConfigs) ? [...section.excludedConfigs] : [];
                return {
                    failureThreshold: normalizedThreshold,
                    currentFailures: failuresPayload,
                    excludedConfigs: excludedPayload
                };
            };

            const servicesPayload = {};
            SERVICE_NAMES.forEach(service => {
                servicesPayload[service] = buildServiceSection(service);
            });

            return {
                mode: loadbalanceConfig.mode,
                services: servicesPayload
            };
        };

        const loadLoadbalanceConfig = async () => {
            loadbalanceLoading.value = true;
            try {
                const data = await fetchWithErrorHandling('/api/loadbalance/config');
                if (data.config) {
                    const normalized = normalizeLoadbalanceConfig(data.config);
                    applyLoadbalanceConfig(normalized);
                }
            } catch (error) {
                console.error('Failed to load load-balancing configuration:', error);
                ElMessage.error('Failed to load load-balancing configuration: ' + error.message);
                applyLoadbalanceConfig(normalizeLoadbalanceConfig({}));
            } finally {
                loadbalanceLoading.value = false;
            }
        };

        const saveLoadbalanceConfig = async (showSuccess = true) => {
            loadbalanceSaving.value = true;
            try {
                const payload = buildLoadbalancePayload();
                const result = await fetchWithErrorHandling('/api/loadbalance/config', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify(payload)
                });

                if (result.success) {
                    if (showSuccess) {
                        ElMessage.success('Load-balancing configuration saved');
                    }
                    await loadLoadbalanceConfig();
                } else {
                    ElMessage.error('Failed to save load-balancing configuration: ' + (result.error || 'Unknown error'));
                }
            } catch (error) {
                ElMessage.error('Failed to save load-balancing configuration: ' + error.message);
            } finally {
                loadbalanceSaving.value = false;
            }
        };

        const selectLoadbalanceMode = async (mode) => {
            if (loadbalanceConfig.mode === mode) {
                return;
            }
            loadbalanceConfig.mode = mode;
            await saveLoadbalanceConfig(false);
            ElMessage.success(`Switched to ${getLoadbalanceModeText(mode)} mode`);
        };

        const weightedTargets = computed(() => {
            const result = {};
            SERVICE_NAMES.forEach(service => {
                const metadata = configMetadata[service] || {};
                const threshold = loadbalanceConfig.services[service]?.failureThreshold || 3;
                const failures = loadbalanceConfig.services[service]?.currentFailures || {};
                const excluded = loadbalanceConfig.services[service]?.excludedConfigs || [];
                const list = Object.entries(metadata).map(([name, meta]) => {
                    const weight = Number(meta?.weight ?? 0);
                    return {
                        name,
                        weight: Number.isFinite(weight) ? weight : 0,
                        failures: failures[name] || 0,
                        threshold,
                        excluded: excluded.includes(name),
                        isActive: services[service].config === name
                    };
                });
                list.sort((a, b) => {
                    if (b.weight !== a.weight) {
                        return b.weight - a.weight;
                    }
                    return a.name.localeCompare(b.name);
                });
                result[service] = list;
            });
            return result;
        });

        const resetLoadbalanceFailures = async (service) => {
            if (resettingFailures[service]) {
                return;
            }
            resettingFailures[service] = true;
            try {
                const result = await fetchWithErrorHandling('/api/loadbalance/reset-failures', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ service })
                });

                if (result.success) {
                    ElMessage.success(result.message || 'Failure counts reset');
                    await loadLoadbalanceConfig();
                } else {
                    ElMessage.error('Failed to reset failure counts: ' + (result.error || 'Unknown error'));
                }
            } catch (error) {
                ElMessage.error('Failed to reset failure counts: ' + error.message);
            } finally {
                resettingFailures[service] = false;
            }
        };

        const pinWeightedTarget = async (service, configName) => {
            if (!service || !configName || !pinningState[service]) {
                return;
            }

            if (pinningState[service].loading) {
                return;
            }

            pinningState[service].loading = true;
            pinningState[service].target = configName;

            try {
                let rawContent = configContents[service];
                if (!rawContent || !rawContent.trim()) {
                    const response = await fetchWithErrorHandling(`/api/config/${service}`);
                    rawContent = response?.content ?? '{}';
                }

                let parsedConfig = {};
                try {
                    parsedConfig = rawContent && rawContent.trim() ? JSON.parse(rawContent) : {};
                } catch (error) {
                    throw new Error('The current configuration file is not valid JSON');
                }

                if (!parsedConfig[configName]) {
                    throw new Error('The specified configuration entry could not be found');
                }

                const currentOrder = weightedTargets.value[service]?.map(item => item.name) || [];
                const existingNames = Object.keys(parsedConfig);

                const orderedNames = [];
                const seen = new Set();

                if (!seen.has(configName)) {
                    orderedNames.push(configName);
                    seen.add(configName);
                }

                currentOrder.forEach(name => {
                    if (!seen.has(name) && parsedConfig[name]) {
                        orderedNames.push(name);
                        seen.add(name);
                    }
                });

                existingNames.forEach(name => {
                    if (!seen.has(name)) {
                        orderedNames.push(name);
                        seen.add(name);
                    }
                });

                if (orderedNames.length === 0) {
                    throw new Error('No configuration entries are available to update');
                }

                const updatedConfig = {};
                orderedNames.forEach((name, index) => {
                    const entry = { ...parsedConfig[name] };
                    entry.weight = Math.max(1, PINNED_WEIGHT_START - index);
                    updatedConfig[name] = entry;
                });

                const newContent = JSON.stringify(updatedConfig, null, 2);
                const saveResult = await fetchWithErrorHandling(`/api/config/${service}`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ content: newContent })
                });

                if (!saveResult?.success) {
                    throw new Error(saveResult?.error || saveResult?.message || 'Failed to save configuration');
                }

                configContents[service] = newContent;

                await loadConfigOptions();
                ElMessage.success(`${configName} pinned to the top`);
            } catch (error) {
                ElMessage.error(`Pin failed: ${error.message}`);
            } finally {
                pinningState[service].loading = false;
                pinningState[service].target = '';
            }
        };

        // API helper methods
        const fetchWithErrorHandling = async (url, options = {}) => {
            try {
                const response = await fetch(url, options);
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                }
                return await response.json();
            } catch (error) {
                console.error(`API request failed ${url}:`, error);
                throw error;
            }
        };
        
        // Load status information
        const loadStatus = async () => {
            try {
                const data = await fetchWithErrorHandling('/api/status');
                updateServiceStatus(data);
                updateStats(data);
            } catch (error) {
                ElMessage.error('Failed to retrieve status: ' + error.message);
            }
        };
        
        // Update service state objects
        const updateServiceStatus = (data) => {
            if (data.services?.claude) {
                Object.assign(services.claude, data.services.claude);
            }
            if (data.services?.legacy) {
                Object.assign(services.legacy, data.services.legacy);
            }
            if (data.services?.codex) {
                Object.assign(services.codex, data.services.codex);
            }
        };
        
        // Refresh statistics values
        const updateStats = (data) => {
            stats.requestCount = data.request_count || 0;
            stats.configCount = data.config_count || 0;
            stats.filterCount = data.filter_count || 0;

            const summary = data.usage_summary || null;
            if (summary) {
                const perService = {};
                const totalMetrics = createEmptyMetrics();
                Object.entries(summary.per_service || {}).forEach(([service, payload]) => {
                    if (!service || service === 'unknown') {
                        return;
                    }
                    const adjusted = adjustUsageBlockForService(service, payload || {});
                    perService[service] = adjusted;
                    mergeMetricsInto(totalMetrics, adjusted.displayMetrics || adjusted.metrics);
                });

                SERVICE_NAMES.forEach(service => {
                    if (!perService[service]) {
                        perService[service] = adjustUsageBlockForService(service, {});
                    }
                });

                usageSummary.perService = perService;

                let totalsBlock;
                if (Object.keys(perService).length === 0 && summary.totals) {
                    totalsBlock = adjustUsageBlockForService('codex', summary.totals || {});
                } else {
                    totalsBlock = updateFormattedFromMetrics({
                        metrics: Object.assign(createEmptyMetrics(), totalMetrics),
                        displayMetrics: Object.assign(createEmptyMetrics(), totalMetrics),
                        formatted: createEmptyFormatted()
                    });
                }
                usageSummary.totals = Object.assign(createEmptyMetrics(), totalsBlock.displayMetrics || totalsBlock.metrics);
                usageSummary.formattedTotals = Object.assign(createEmptyFormatted(), totalsBlock.displayFormatted || totalsBlock.formatted);
            } else {
                resetUsageSummary();
            }
        };
        
        // Load logs
        const loadLogs = async () => {
            logsLoading.value = true;
            try {
                const data = await fetchWithErrorHandling('/api/logs');
                logs.value = Array.isArray(data) ? data : [];
            } catch (error) {
                ElMessage.error('Failed to fetch logs: ' + error.message);
                logs.value = [];
            } finally {
                logsLoading.value = false;
            }
        };
        
        // Load configuration options
        const loadConfigOptions = async () => {
            try {
                const configRefs = {
                    claude: claudeConfigs,
                    legacy: legacyConfigs,
                    codex: codexConfigs,
                };

                await Promise.all(SERVICE_NAMES.map(async (service) => {
                    const response = await fetchWithErrorHandling(`/api/config/${service}`);
                    const targetRef = configRefs[service];
                    if (!targetRef) {
                        return;
                    }

                    if (response.content) {
                        const configs = JSON.parse(response.content);
                        const entries = Object.entries(configs).filter(
                            ([key, value]) => key && key !== 'undefined' && value !== undefined
                        );
                        targetRef.value = entries.map(([key]) => key);
                        const metadata = {};
                        entries.forEach(([key, value]) => {
                            const weightValue = Number(value?.weight ?? 0);
                            const rpmValue = Number(value?.rpm_limit ?? value?.requests_per_minute ?? 0);
                            metadata[key] = {
                                weight: Number.isFinite(weightValue) ? weightValue : 0,
                                active: !!value?.active,
                                rpmLimit: Number.isFinite(rpmValue) && rpmValue > 0 ? rpmValue : null,
                            };
                        });
                        configMetadata[service] = metadata;
                    } else {
                        targetRef.value = [];
                        configMetadata[service] = {};
                    }
                }));
            } catch (error) {
                console.error('Failed to load configuration options:', error);
            }
        };
        
        // Load all core data
        const loadData = async () => {
            loading.value = true;
            try {
                await loadConfigOptions();
                await Promise.all([
                    loadStatus(),
                    loadLogs(),
                    loadRoutingConfig(),
                    loadLoadbalanceConfig()
                ]);
                updateLastUpdateTime();
            } catch (error) {
                console.error('Failed to load data:', error);
                ElMessage.error('Failed to load data');
            } finally {
                loading.value = false;
            }
        };
        
        // Refresh the page
        const refreshData = () => {
            window.location.reload();
        };
        
        // Update the "last updated" timestamp
        const updateLastUpdateTime = () => {
            const now = new Date();
            const timeString = now.toLocaleTimeString(undefined, { hour12: false });
            lastUpdate.value = `Last updated: ${timeString}`;
        };
        
        // Switch active configuration
        const switchConfig = async (serviceName, configName) => {
            if (!configName) return;
            if (isLoadbalanceWeightMode.value) {
                ElMessage.info('Load-balancing weight mode is active; manual switching is disabled');
                return;
            }
            
            try {
                const result = await fetchWithErrorHandling('/api/switch-config', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        service: serviceName,
                        config: configName
                    })
                });
                
                if (result.success) {
                    ElMessage.success(`${serviceName} configuration switched to ${configName}`);
                    // Update local state to avoid unnecessary reloads
                    services[serviceName].config = configName;
                    updateLastUpdateTime();
                } else {
                    ElMessage.error(result.message || 'Configuration switch failed');
                    // Restore original selection on failure
                    await loadStatus();
                }
            } catch (error) {
                ElMessage.error('Configuration switch failed: ' + error.message);
                // Restore original selection after an error
                await loadStatus();
            }
        };
        
        // Config drawer helpers
        const openConfigDrawer = async () => {
            configDrawerVisible.value = true;
            await loadConfigs();
        };
        
        const closeConfigDrawer = () => {
            configDrawerVisible.value = false;
        };
        
        const loadConfigs = async () => {
            try {
                await Promise.all(SERVICE_NAMES.map(async (service) => {
                    const data = await fetchWithErrorHandling(`/api/config/${service}`);
                    const content = data?.content ?? '{}';
                    const normalized = content.trim() ? content : '{}';
                    configContents[service] = normalized;
                    syncJsonToForm(service);
                }));
            } catch (error) {
                const errorMsg = '// Load failed: ' + error.message;
                SERVICE_NAMES.forEach(service => {
                    configContents[service] = errorMsg;
                    friendlyConfigs[service] = [];
                });
            }
        };
        
        // Friendly form configuration helpers
        const buildDefaultSite = (service) => {
            const base = {
                name: '',
                baseUrl: 'https://',
                authType: 'auth_token',
                authValue: '',
                active: false,
                weight: 0
            };
            if (service === 'legacy') {
                base.rpmLimit = 0;
            }
            return base;
        };

        const startAddingSite = (service) => {
            editingNewSite[service] = true;
            newSiteData[service] = buildDefaultSite(service);
            // Auto-focus the site name input
            nextTick(() => {
                const input = document.querySelector('.new-site-name-input input');
                if (input) {
                    input.focus();
                }
            });
        };

        const confirmAddSite = async (service) => {
            if (newSiteData[service].name.trim()) {
                // If the new site is marked active, deactivate all others first
                if (newSiteData[service].active) {
                    friendlyConfigs[service].forEach(site => {
                        site.active = false;
                    });
                }
                // Append to the list and re-sort
                const siteWithId = {
                    ...newSiteData[service],
                    __mergedId: generateEntryId()
                };
                friendlyConfigs[service].push(siteWithId);
                sortFriendlyList(service);
                editingNewSite[service] = false;
                syncFormToJson(service);

                // Persist the configuration
                await saveConfigForService(service);
            }
        };

        const cancelAddSite = (service) => {
            editingNewSite[service] = false;
            newSiteData[service] = buildDefaultSite(service);
        };

        const saveInteractiveConfig = async (service) => {
            await saveConfigForService(service);
        };

        const removeConfigSite = async (service, index) => {
            const siteName = friendlyConfigs[service][index]?.name || 'Unnamed site';
            try {
                await ElMessageBox.confirm(
                    `Delete site "${siteName}"? This action cannot be undone.`,
                    'Confirm site deletion',
                    {
                        confirmButtonText: 'Confirm',
                        cancelButtonText: 'Cancel',
                        type: 'warning',
                    }
                );
                
                friendlyConfigs[service].splice(index, 1);
                sortFriendlyList(service);
                syncFormToJson(service);
                
                // Persist the configuration
                await saveConfigForService(service);
                
                ElMessage.success('Site removed successfully');
            } catch (error) {
                if (error !== 'cancel') {
                    ElMessage.error('Failed to delete site: ' + error.message);
                }
            }
        };

        // Handle single-active selection changes
        const handleActiveChange = (service, activeIndex, newValue) => {
            if (newValue) {
                // Activating this site disables all others
                friendlyConfigs[service].forEach((site, index) => {
                    if (index !== activeIndex) {
                        site.active = false;
                    }
                });
            } else {
                friendlyConfigs[service][activeIndex].active = false;
            }
            sortFriendlyList(service);
            syncFormToJson(service);
        };

        // Sync from form state into JSON
        const syncFormToJson = (service) => {
            if (syncInProgress.value) return;

            try {
                syncInProgress.value = true;
                sortFriendlyList(service);
                const jsonObj = {};
                friendlyConfigs[service].forEach(site => {
                    if (site.name && site.name.trim()) {
                        const config = {
                            base_url: site.baseUrl || '',
                            active: site.active || false
                        };

                        // Set auth fields according to auth type
                        if (site.authType === 'auth_token') {
                            config.auth_token = site.authValue || '';
                            config.api_key = '';
                        } else {
                            config.api_key = site.authValue || '';
                            config.auth_token = '';
                        }

                        const weightValue = Number(site.weight ?? 0);
                        config.weight = Number.isFinite(weightValue) ? weightValue : 0;

                        if (service === 'legacy') {
                            const rpmValue = Number(site.rpmLimit ?? configMetadata.legacy?.[site.name]?.rpmLimit ?? 0);
                            if (Number.isFinite(rpmValue) && rpmValue > 0) {
                                config.rpm_limit = rpmValue;
                            }
                            // Add streaming configuration
                            if (site.streaming !== undefined) {
                                config.streaming = site.streaming;
                            }
                            // Add tool calls streaming configuration
                            if (site.toolCallsStreaming !== undefined) {
                                config.tool_calls_streaming = site.toolCallsStreaming;
                            }
                        }

                        jsonObj[site.name.trim()] = config;
                    }
                });

                configContents[service] = JSON.stringify(jsonObj, null, 2);
            } catch (error) {
                console.error('Failed to sync form data to JSON:', error);
            } finally {
                // Reset after a tick so watchers do not trigger immediately
                nextTick(() => {
                    syncInProgress.value = false;
                });
            }
        };

        // Sync from JSON into form state
        const syncJsonToForm = (service) => {
            if (syncInProgress.value) return;

            try {
                syncInProgress.value = true;
                const content = configContents[service];
                if (!content || content.trim() === '' || content.trim() === '{}') {
                    friendlyConfigs[service] = [];
                    return;
                }

                const jsonObj = JSON.parse(content);
                const sites = [];

                Object.entries(jsonObj).forEach(([siteName, config]) => {
                    if (config && typeof config === 'object') {
                        // Determine which authentication strategy to use
                        let authType = 'auth_token';
                        let authValue = '';

                        if (config.api_key && config.api_key.trim()) {
                            authType = 'api_key';
                            authValue = config.api_key;
                        } else if (config.auth_token) {
                            authType = 'auth_token';
                            authValue = config.auth_token;
                        }

                        let weightValue = Number(config.weight ?? 0);
                        if (!Number.isFinite(weightValue)) {
                            weightValue = 0;
                        }

                        let rpmLimit = null;
                        if (service === 'legacy') {
                            const rawRpm = Number(config.rpm_limit ?? config.requests_per_minute ?? 0);
                            rpmLimit = Number.isFinite(rawRpm) && rawRpm > 0 ? rawRpm : 0;
                        }

                        const siteObj = {
                            name: siteName,
                            baseUrl: config.base_url || '',
                            authType: authType,
                            authValue: authValue,
                            active: config.active || false,
                            weight: weightValue,
                            rpmLimit: rpmLimit,
                            __mergedId: generateEntryId()
                        };

                        if (service === 'legacy') {
                            if (config.streaming !== undefined) {
                                siteObj.streaming = config.streaming;
                            }
                            if (config.tool_calls_streaming !== undefined) {
                                siteObj.toolCallsStreaming = config.tool_calls_streaming;
                            }
                        }

                        sites.push(siteObj);
                    }
                });

                friendlyConfigs[service] = sites;
                sortFriendlyList(service);
            } catch (error) {
                console.error('Failed to sync JSON to form:', error);
                // Leave existing form data untouched if parsing fails
            } finally {
                // Reset flag on the next tick
                nextTick(() => {
                    syncInProgress.value = false;
                });
            }
        };

        // ========== Merged mode helpers ==========

        /**
         * Helper for validating URLs
         */
        const isValidUrl = (str) => {
            try {
                const url = new URL(str);
                return url.protocol === 'http:' || url.protocol === 'https:';
            } catch {
                return false;
            }
        };

        const generateEntryId = () => `merged-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;

        const ensureEntryId = (site) => {
            if (!site.__mergedId) {
                site.__mergedId = generateEntryId();
            }
            return site.__mergedId;
        };

        const findFriendlyBySiteId = (service, siteId) => (
            friendlyConfigs[service].find(site => site.__mergedId === siteId)
        );

        const findFriendlyIndexBySiteId = (service, siteId) => (
            friendlyConfigs[service].findIndex(site => site.__mergedId === siteId)
        );

        const normalizeValue = (value) => (value ?? '').toString().trim();

        const getWeightValue = (value) => {
            const num = Number(value);
            return Number.isFinite(num) ? num : 0;
        };

        const compareByName = (left, right) => (
            normalizeValue(left).localeCompare(normalizeValue(right), 'en')
        );

        const sortEntryList = (entries = []) => {
            entries.sort((a, b) => {
                if (a.active !== b.active) {
                    return a.active ? -1 : 1;
                }
                return compareByName(a.name, b.name);
            });
        };

        const sortFriendlyList = (service) => {
            friendlyConfigs[service].sort((a, b) => {
                if (a.active !== b.active) {
                    return a.active ? -1 : 1;
                }

                const weightDiff = getWeightValue(b.weight) - getWeightValue(a.weight);
                if (weightDiff !== 0) {
                    return weightDiff;
                }

                const nameCompare = compareByName(a.name, b.name);
                if (nameCompare !== 0) {
                    return nameCompare;
                }

                return compareByName(a.baseUrl, b.baseUrl);
            });
        };

        const sortMergedGroups = (service) => {
            mergedConfigs[service].forEach(group => {
                if (Array.isArray(group.entries)) {
                    sortEntryList(group.entries);
                }
            });

            mergedConfigs[service].sort((a, b) => {
                const aActive = Array.isArray(a.entries) && a.entries.some(entry => entry.active);
                const bActive = Array.isArray(b.entries) && b.entries.some(entry => entry.active);

                if (aActive !== bActive) {
                    return aActive ? -1 : 1;
                }

                const weightDiff = getWeightValue(b.weight) - getWeightValue(a.weight);
                if (weightDiff !== 0) {
                    return weightDiff;
                }

                return compareByName(a.baseUrl, b.baseUrl);
            });
        };

        /**
         * Ensure every entry in the group uses the same weight and auth type
         * Returns a list of fields that required normalization
         */
        const normalizeGroupValues = (service, group) => {
            const inconsistent = [];
            const standardWeight = group.weight;
            const standardAuthType = group.authType;
            const standardRpm = service === 'legacy'
                ? (Number.isFinite(group.rpmLimit) && group.rpmLimit > 0 ? group.rpmLimit : 0)
                : null;

            group.entries.forEach(entry => {
                // Locate the friendly entry and align it with the group values
                const friendlyItem = findFriendlyBySiteId(service, entry.siteId);

                if (friendlyItem) {
                    if (friendlyItem.weight !== standardWeight) {
                        if (!inconsistent.includes('weight')) {
                            inconsistent.push('weight');
                        }
                        friendlyItem.weight = standardWeight;
                    }
                    if (friendlyItem.authType !== standardAuthType) {
                        if (!inconsistent.includes('auth type')) {
                            inconsistent.push('auth type');
                        }
                        friendlyItem.authType = standardAuthType;
                    }
                    if (service === 'legacy') {
                        const friendlyRpm = Number.isFinite(friendlyItem.rpmLimit) ? friendlyItem.rpmLimit : 0;
                        if (friendlyRpm !== standardRpm) {
                            if (!inconsistent.includes('rpm limit')) {
                                inconsistent.push('rpm limit');
                            }
                            friendlyItem.rpmLimit = standardRpm;
                        }
                    }
                }
            });

            // Sync back to JSON when changes were required
            if (inconsistent.length > 0) {
                syncFormToJson(service);
            }

            return inconsistent;
        };

        /**
         * Check whether any secrets in the group are active
         */
        const isMergedGroupActive = (group) => (
            group && Array.isArray(group.entries) && group.entries.some(entry => entry.active)
        );

        /**
         * Build merged view from interactive configuration
         */
        const buildMergedFromFriendly = (service) => {
            const friendly = friendlyConfigs[service];
            const grouped = new Map(); // baseUrl -> group object

            sortFriendlyList(service);

            // Step 1: group by baseUrl
            friendly.forEach((site, idx) => {
                if (!site.baseUrl || !site.baseUrl.trim()) {
                    console.warn(`Site ${site.name} is missing base_url`);
                    return;
                }

                const key = site.baseUrl.trim();
                if (!grouped.has(key)) {
                    grouped.set(key, {
                        baseUrl: key,
                        weight: site.weight || 0,
                        authType: site.authType || 'auth_token',
                        rpmLimit: service === 'legacy'
                            ? (Number.isFinite(site.rpmLimit) && site.rpmLimit > 0 ? site.rpmLimit : 0)
                            : null,
                        streaming: service === 'legacy'
                            ? (site.streaming !== undefined ? site.streaming : null)
                            : null,
                        toolCallsStreaming: service === 'legacy'
                            ? (site.toolCallsStreaming !== undefined ? site.toolCallsStreaming : null)
                            : null,
                        entries: []
                    });
                }

                const group = grouped.get(key);
                if (service === 'legacy') {
                    const candidateRpm = Number.isFinite(site.rpmLimit) && site.rpmLimit > 0 ? site.rpmLimit : 0;
                    if (candidateRpm > 0) {
                        group.rpmLimit = candidateRpm;
                    } else if (!Number.isFinite(group.rpmLimit)) {
                        group.rpmLimit = 0;
                    }
                }
                const siteId = ensureEntryId(site);

                group.entries.push({
                    name: site.name,
                    authValue: site.authValue || '',
                    active: site.active || false,
                    derivedId: site.__mergedId || `${site.name}_${idx}`,
                    siteId,
                    lastSyncedName: site.name
                });
            });

            // Step 2: normalize weight and authType inside each group
            grouped.forEach((group, baseUrl) => {
                const inconsistent = normalizeGroupValues(service, group);
                if (inconsistent.length > 0) {
                    ElMessage.warning(
                        `Site group ${baseUrl} had inconsistent ${inconsistent.join(', ')}; normalized to the first entry`
                    );
                }
            });

            // Step 3: update mergedConfigs
            mergedConfigs[service] = Array.from(grouped.values());
            sortMergedGroups(service);
        };

        /**
         * Rebuild interactive configuration from merged view
         * Used prior to saving for validation and reconstruction
         */
        const rebuildFriendlyFromMerged = (service) => {
            const merged = mergedConfigs[service];
            const newFriendly = [];

            merged.forEach(group => {
                group.entries.forEach(entry => {
                    const siteId = entry.siteId || generateEntryId();
                    entry.siteId = siteId;
                    entry.lastSyncedName = entry.name;
                    const newSiteObj = {
                        name: entry.name,
                        baseUrl: group.baseUrl,
                        weight: group.weight,
                        authType: group.authType,
                        authValue: entry.authValue,
                        active: entry.active,
                        rpmLimit: service === 'legacy'
                            ? (Number.isFinite(group.rpmLimit) && group.rpmLimit > 0 ? group.rpmLimit : 0)
                            : undefined,
                        __mergedId: siteId
                    };

                    if (service === 'legacy') {
                        if (group.streaming !== undefined) {
                            newSiteObj.streaming = group.streaming;
                        }
                        if (group.toolCallsStreaming !== undefined) {
                            newSiteObj.toolCallsStreaming = group.toolCallsStreaming;
                        }
                    }

                    newFriendly.push(newSiteObj);
                });
            });

            friendlyConfigs[service] = newFriendly;
            sortFriendlyList(service);
            syncFormToJson(service);
        };

        /**
         * Apply group-level changes (weight, auth type, base_url)
         */
        const applyMergedGroupUpdate = (service, groupIndex) => {
            const group = mergedConfigs[service][groupIndex];

            // Validate URL
            if (!isValidUrl(group.baseUrl)) {
                ElMessage.warning('Target address is invalid; please enter a valid HTTP/HTTPS URL');
                return;
            }

            if (service === 'legacy') {
                if (group.rpmLimit === '' || group.rpmLimit === undefined) {
                    group.rpmLimit = 0;
                }
                const rpmValue = Number(group.rpmLimit);
                if (!Number.isFinite(rpmValue) || rpmValue < 0) {
                    ElMessage.warning('RPM limit must be zero (unlimited) or a positive number');
                    return;
                }
                group.rpmLimit = rpmValue;
            }

            // Update every matching friendly configuration entry
            group.entries.forEach(entry => {
                const friendlyItem = findFriendlyBySiteId(service, entry.siteId);

                if (friendlyItem) {
                    friendlyItem.baseUrl = group.baseUrl;
                    friendlyItem.weight = group.weight;
                    friendlyItem.authType = group.authType;
                    if (service === 'legacy') {
                        const rpmValue = Number(group.rpmLimit);
                        friendlyItem.rpmLimit = Number.isFinite(rpmValue) ? rpmValue : 0;
                    }
                }
            });

            sortFriendlyList(service);
            sortMergedGroups(service);
            // Sync the change back to JSON
            syncFormToJson(service);
        };

        /**
         * Automatically generate a site name
         * Strategy: look at existing names with the same baseUrl prefix and increment the suffix
         */
        const autoGenerateName = (service, baseUrl) => {
            try {
                const hostname = new URL(baseUrl).hostname;
                const prefix = hostname.split('.')[0]; // e.g. "api" or "privnode"

                // Find all site names starting with the prefix
                const existingNames = friendlyConfigs[service]
                    .filter(site => site.baseUrl === baseUrl)
                    .map(site => site.name);

                // Extract numeric suffixes
                const numbers = existingNames
                    .map(name => {
                        const match = name.match(/(\d+)$/);
                        return match ? parseInt(match[1]) : 0;
                    })
                    .filter(n => n > 0);

                const maxNum = numbers.length > 0 ? Math.max(...numbers) : 0;
                return `${prefix}-${maxNum + 1}`;

            } catch (e) {
                // Fallback to random name when URL parsing fails
                return `site-${Date.now()}`;
            }
        };

        /**
         * Global helper that enforces a single active site within a service
         */
        const enforceSingleActive = (service, targetSiteId) => {
            friendlyConfigs[service].forEach(site => {
                site.active = (site.__mergedId === targetSiteId);
            });

            sortFriendlyList(service);
            // Rebuild the merged view
            buildMergedFromFriendly(service);
            sortMergedGroups(service);

            // Sync back to JSON
            syncFormToJson(service);
        };

        /**
         * Toggle the active state within merged mode
         */
        const toggleMergedActive = (service, groupIndex, entryIndex, newValue) => {
            const entry = mergedConfigs[service][groupIndex].entries[entryIndex];

            if (newValue) {
                // Warn that enabling this entry disables all others
                ElMessageBox.confirm(
                    'Activating this secret will disable all other sites. Continue?',
                    'Notice',
                    { type: 'warning' }
                ).then(() => {
                    enforceSingleActive(service, entry.siteId);
                }).catch(() => {
                    // Revert when the user cancels
                    entry.active = false;
                });
            } else {
                // Deactivate the entry
                const friendlyItem = findFriendlyBySiteId(service, entry.siteId);
                if (friendlyItem) {
                    friendlyItem.active = false;
                    sortFriendlyList(service);
                    sortMergedGroups(service);
                    syncFormToJson(service);
                }
            }
        };

        /**
         * Update the secret value and sync to friendlyConfigs
         */
        const updateMergedEntryAuth = (service, groupIndex, entryIndex) => {
            const group = mergedConfigs[service][groupIndex];
            const entry = group.entries[entryIndex];

            // Find and update the friendly entry
            const friendlyItem = findFriendlyBySiteId(service, entry.siteId);

            if (friendlyItem) {
                friendlyItem.authValue = entry.authValue;
                syncFormToJson(service);
            }
        };

        /**
         * Update a site name (enforcing uniqueness and syncing)
         */
        const applyMergedEntryNameUpdate = (service, groupIndex, entryIndex) => {
            const entry = mergedConfigs[service][groupIndex].entries[entryIndex];
            const previousName = entry.lastSyncedName || '';
            const trimmed = (entry.name || '').trim();

            if (!trimmed) {
                ElMessage.warning('Site name cannot be empty');
                entry.name = previousName;
                return;
            }

            if (trimmed === previousName) {
                entry.name = trimmed;
                return;
            }

            const hasConflict = friendlyConfigs[service].some(site => (
                site.__mergedId !== entry.siteId && site.name === trimmed
            ));

            if (hasConflict) {
                ElMessage.error(`Site name "${trimmed}" already exists; please choose another`);
                entry.name = previousName;
                return;
            }

            entry.name = trimmed;

            const friendlyItem = findFriendlyBySiteId(service, entry.siteId);
            if (friendlyItem) {
                friendlyItem.name = trimmed;
                entry.lastSyncedName = trimmed;
                sortEntryList(mergedConfigs[service][groupIndex].entries);
                sortFriendlyList(service);
                sortMergedGroups(service);
                syncFormToJson(service);
            } else {
                entry.lastSyncedName = trimmed;
                sortEntryList(mergedConfigs[service][groupIndex].entries);
                sortMergedGroups(service);
            }
        };

        /**
         * Add a secret entry to an existing group
         */
        const addMergedEntry = (service, groupIndex) => {
            const group = mergedConfigs[service][groupIndex];

            // Generate a default name for the new site
            const newName = autoGenerateName(service, group.baseUrl);

            const newSiteId = generateEntryId();
            const newEntry = {
                name: newName,
                authValue: '',
                active: false,
                derivedId: newSiteId,
                siteId: newSiteId,
                lastSyncedName: newName
            };

            group.entries.push(newEntry);
            sortEntryList(group.entries);

            // Mirror the change into friendlyConfigs
            friendlyConfigs[service].push({
                name: newEntry.name,
                baseUrl: group.baseUrl,
                weight: group.weight,
                authType: group.authType,
                authValue: '',
                active: false,
                rpmLimit: service === 'legacy'
                    ? (Number.isFinite(group.rpmLimit) ? group.rpmLimit : 0)
                    : undefined,
                __mergedId: newSiteId
            });
            sortFriendlyList(service);
            sortMergedGroups(service);
            syncFormToJson(service);
        };

        /**
         * Remove a secret entry
         */
        const removeMergedEntry = (service, groupIndex, entryIndex) => {
            const group = mergedConfigs[service][groupIndex];
            const entry = group.entries[entryIndex];

            ElMessageBox.confirm(
                `Delete secret "${entry.name}"?`,
                'Notice',
                { type: 'warning' }
            ).then(() => {
                // Remove from merged view
                group.entries.splice(entryIndex, 1);

                // Remove from friendlyConfigs
                const friendlyIndex = findFriendlyIndexBySiteId(service, entry.siteId);
                if (friendlyIndex > -1) {
                    friendlyConfigs[service].splice(friendlyIndex, 1);
                }

                // Delete the entire group if no entries remain
                if (group.entries.length === 0) {
                    mergedConfigs[service].splice(groupIndex, 1);
                    ElMessage.info('The site group no longer has secrets and was removed automatically');
                }

                sortFriendlyList(service);
                sortMergedGroups(service);
                syncFormToJson(service);
                ElMessage.success('Deletion successful');

            }).catch(() => {});
        };

        /**
         * Remove an entire group
         */
        const deleteMergedGroup = (service, groupIndex) => {
            const group = mergedConfigs[service][groupIndex];

            ElMessageBox.confirm(
                `Delete site group "${group.baseUrl}" and all ${group.entries.length} secrets?`,
                'Notice',
                { type: 'warning' }
            ).then(async () => {
                // Remove every linked friendlyConfigs entry
                group.entries.forEach(entry => {
                    const index = findFriendlyIndexBySiteId(service, entry.siteId);
                    if (index > -1) {
                        friendlyConfigs[service].splice(index, 1);
                    }
                });

                // Remove the group itself
                mergedConfigs[service].splice(groupIndex, 1);

                sortFriendlyList(service);
                sortMergedGroups(service);
                syncFormToJson(service);

                try {
                    await saveConfigForService(service);
                    buildMergedFromFriendly(service);
                    ElMessage.success('Site group deleted successfully');
                } catch (error) {
                    console.error('Failed to save after deleting site group:', error);
                }

            }).catch(() => {});
        };

        /**
         * Switch to merged mode
         */
        const switchToMergedMode = () => {
            configEditMode.value = 'merged';
            buildMergedFromFriendly(activeConfigTab.value);
        };

        /**
         * Persist merged mode configuration
         */
        const saveMergedConfig = async (service) => {
            // Step 1: validate each group
            for (const group of mergedConfigs[service]) {
                // Validate baseUrl
                if (!isValidUrl(group.baseUrl)) {
                    ElMessage.error(`Site group ${group.baseUrl} has an invalid address format`);
                    return;
                }

                // Validate weight
                if (!Number.isFinite(group.weight) || group.weight < 0) {
                    ElMessage.error(`Site group ${group.baseUrl} must have a non-negative integer weight`);
                    return;
                }

                // Ensure each secret is filled in
                const invalidEntries = group.entries.filter(
                    entry => !entry.authValue || !entry.authValue.trim()
                );
                if (invalidEntries.length > 0) {
                    ElMessage.error(
                        `Site group ${group.baseUrl} has empty secrets: ${invalidEntries.map(e => e.name).join(', ')}`
                    );
                    return;
                }
            }

            // Step 2: rebuild friendlyConfigs from the merged view
            rebuildFriendlyFromMerged(service);

            // Step 3: reuse the existing save workflow
            await saveConfigForService(service);
        };

        /**
         * Open dialog for adding a site
         */
        const openMergedDialog = (service) => {
            mergedDialogService.value = service;
            mergedDialogMode.value = 'add';
            mergedDialogEditIndex.value = -1;
            mergedDialogDraft.baseUrl = 'https://';
            mergedDialogDraft.weight = 0;
            mergedDialogDraft.authType = 'auth_token';
            mergedDialogDraft.rpmLimit = service === 'legacy' ? 0 : null;
            mergedDialogDraft.streaming = service === 'legacy' ? null : null;
            mergedDialogDraft.entries = [
                { name: '', authValue: '', active: false, siteId: null, lastSyncedName: '' }
            ];
            mergedDialogVisible.value = true;
        };

        /**
         * Open editing dialog
         */
        const editMergedGroup = (service, groupIndex) => {
            const group = mergedConfigs[service]?.[groupIndex];
            if (!group) {
                ElMessage.error('Matching site group not found');
                return;
            }

            mergedDialogService.value = service;
            mergedDialogMode.value = 'edit';
            mergedDialogEditIndex.value = groupIndex;

            mergedDialogDraft.baseUrl = group.baseUrl;
            mergedDialogDraft.weight = group.weight;
            mergedDialogDraft.authType = group.authType;
            mergedDialogDraft.rpmLimit = service === 'legacy'
                ? (Number.isFinite(group.rpmLimit) ? group.rpmLimit : 0)
                : null;
            mergedDialogDraft.streaming = service === 'legacy'
                ? (group.streaming !== undefined ? group.streaming : null)
                : null;
            mergedDialogDraft.entries = group.entries.length > 0
                ? group.entries.map(entry => ({
                    name: entry.name,
                    authValue: entry.authValue,
                    active: entry.active,
                    siteId: entry.siteId || null,
                    lastSyncedName: entry.lastSyncedName || entry.name
                }))
                : [{ name: '', authValue: '', active: false, siteId: null, lastSyncedName: '' }];

            sortEntryList(mergedDialogDraft.entries);

            mergedDialogVisible.value = true;
        };

        /**
         * Add a secret row inside the dialog
         */
        const addDialogEntry = () => {
            mergedDialogDraft.entries.push({
                name: '',
                authValue: '',
                active: false,
                siteId: null,
                lastSyncedName: ''
            });
        };

        /**
         * Remove a secret row from the dialog
         */
        const removeDialogEntry = (index) => {
            if (mergedDialogDraft.entries.length <= 1) {
                ElMessage.warning('Keep at least one secret');
                return;
            }
            mergedDialogDraft.entries.splice(index, 1);
        };

        /**
         * Cancel the dialog
         */
        const cancelMergedDialog = () => {
            mergedDialogVisible.value = false;
            mergedDialogMode.value = 'add';
            mergedDialogEditIndex.value = -1;
        };

        /**
         * Submit the site group form
         */
        const submitMergedDialog = async () => {
            const service = mergedDialogService.value;
            const draft = mergedDialogDraft;
            const mode = mergedDialogMode.value;

            if (!service) {
                ElMessage.error('No service selected to save');
                return;
            }

            if (!isValidUrl(draft.baseUrl)) {
                ElMessage.error('Target address is invalid; please provide a valid HTTP/HTTPS URL');
                return;
            }

            if (!Number.isFinite(draft.weight) || draft.weight < 0) {
                ElMessage.error('Weight must be a non-negative integer');
                return;
            }

            if (service === 'legacy') {
                if (draft.rpmLimit === '' || draft.rpmLimit === undefined || draft.rpmLimit === null) {
                    draft.rpmLimit = 0;
                }
                const rpmValue = Number(draft.rpmLimit);
                if (!Number.isFinite(rpmValue) || rpmValue < 0) {
                    ElMessage.error('RPM limit must be zero (unlimited) or a positive number');
                    return;
                }
                draft.rpmLimit = rpmValue;
            } else {
                draft.rpmLimit = null;
            }

            if (!Array.isArray(draft.entries) || draft.entries.length === 0) {
                ElMessage.error('Keep at least one secret');
                return;
            }

            draft.entries.forEach(entry => {
                entry.name = (entry.name || '').trim();
                entry.authValue = (entry.authValue || '').trim();
                entry.lastSyncedName = entry.lastSyncedName || entry.name;
            });

            sortEntryList(draft.entries);

            const invalidEntries = draft.entries.filter(entry => !entry.name || !entry.authValue);
            if (invalidEntries.length > 0) {
                ElMessage.error('Every secret requires both a site name and secret value');
                return;
            }

            const nameCounts = new Map();
            for (const entry of draft.entries) {
                const count = (nameCounts.get(entry.name) || 0) + 1;
                nameCounts.set(entry.name, count);
                if (count > 1) {
                    ElMessage.error('Site names must be unique within the group');
                    return;
                }
            }

            const entrySiteIds = new Set(
                draft.entries
                    .map(entry => entry.siteId)
                    .filter(Boolean)
            );

            const existingNames = friendlyConfigs[service]
                .filter(site => !entrySiteIds.has(site.__mergedId))
                .map(site => site.name);

            const duplicateEntries = draft.entries.filter(entry => existingNames.includes(entry.name));
            if (duplicateEntries.length > 0) {
                try {
                    await ElMessageBox.confirm(
                        `Detected duplicate site names: ${duplicateEntries.map(e => e.name).join(', ')}. Automatically append suffixes?`,
                        'Notice',
                        { type: 'warning' }
                    );

                    duplicateEntries.forEach(entry => {
                        let suffix = 1;
                        let newName = `${entry.name}-${suffix}`;
                        while (
                            existingNames.includes(newName) ||
                            draft.entries.some(other => other !== entry && other.name === newName)
                        ) {
                            suffix++;
                            newName = `${entry.name}-${suffix}`;
                        }
                        existingNames.push(newName);
                        entry.name = newName;
                        entry.lastSyncedName = newName;
                    });

                } catch {
                    return;
                }
            }

            let activeEntry = null;
            draft.entries.forEach(entry => {
                if (entry.active && !activeEntry) {
                    activeEntry = entry;
                } else if (entry.active && activeEntry) {
                    entry.active = false;
                }
            });

            if (mode === 'add') {
                const newSites = draft.entries.map(entry => {
                    const siteId = generateEntryId();
                    entry.siteId = siteId;
                    entry.lastSyncedName = entry.name;
                    return {
                        name: entry.name,
                        baseUrl: draft.baseUrl,
                        weight: draft.weight,
                        authType: draft.authType,
                        authValue: entry.authValue,
                        active: entry.active,
                        rpmLimit: service === 'legacy' ? draft.rpmLimit : undefined,
                        streaming: service === 'legacy' ? draft.streaming : undefined,
                        __mergedId: siteId
                    };
                });

                friendlyConfigs[service].push(...newSites);
                sortFriendlyList(service);
                sortMergedGroups(service);

                if (activeEntry) {
                    enforceSingleActive(service, activeEntry.siteId);
                } else {
                    syncFormToJson(service);
                }

            } else {
                const groupIndex = mergedDialogEditIndex.value;
                if (groupIndex < 0 || !mergedConfigs[service]?.[groupIndex]) {
                    ElMessage.error('Site group to edit does not exist');
                    return;
                }

                const originalGroup = mergedConfigs[service][groupIndex];
                const draftSiteIds = new Set(draft.entries.map(entry => entry.siteId).filter(Boolean));

                originalGroup.entries.forEach(entry => {
                    if (!draftSiteIds.has(entry.siteId)) {
                        const friendlyIndex = findFriendlyIndexBySiteId(service, entry.siteId);
                        if (friendlyIndex > -1) {
                            friendlyConfigs[service].splice(friendlyIndex, 1);
                        }
                    }
                });

                draft.entries.forEach(entry => {
                    if (entry.siteId) {
                        const friendlyItem = findFriendlyBySiteId(service, entry.siteId);
                        if (friendlyItem) {
                            friendlyItem.name = entry.name;
                            friendlyItem.baseUrl = draft.baseUrl;
                            friendlyItem.weight = draft.weight;
                            friendlyItem.authType = draft.authType;
                            friendlyItem.authValue = entry.authValue;
                            friendlyItem.active = entry.active;
                            if (service === 'legacy') {
                                friendlyItem.rpmLimit = draft.rpmLimit;
                                friendlyItem.streaming = draft.streaming;
                                friendlyItem.toolCallsStreaming = draft.toolCallsStreaming;
                            }
                            friendlyItem.__mergedId = entry.siteId;
                        }
                    } else {
                        const newSiteId = generateEntryId();
                        entry.siteId = newSiteId;
                        entry.lastSyncedName = entry.name;
                        friendlyConfigs[service].push({
                            name: entry.name,
                            baseUrl: draft.baseUrl,
                            weight: draft.weight,
                            authType: draft.authType,
                            authValue: entry.authValue,
                            active: entry.active,
                            rpmLimit: service === 'legacy' ? draft.rpmLimit : undefined,
                            streaming: service === 'legacy' ? draft.streaming : undefined,
                            toolCallsStreaming: service === 'legacy' ? draft.toolCallsStreaming : undefined,
                            __mergedId: newSiteId
                        });
                    }
                });

                sortFriendlyList(service);
                sortMergedGroups(service);

                if (activeEntry) {
                    enforceSingleActive(service, activeEntry.siteId);
                } else {
                    draft.entries.forEach(entry => {
                        if (entry.siteId) {
                            const friendlyItem = findFriendlyBySiteId(service, entry.siteId);
                            if (friendlyItem) {
                                friendlyItem.active = entry.active;
                            }
                        }
                    });
                    syncFormToJson(service);
                }
            }

            configSaving.value = true;
            try {
                await saveConfigForService(service);
                buildMergedFromFriendly(service);
                ElMessage.success(mode === 'edit' ? 'Site group updated successfully' : 'Site group added successfully');
                mergedDialogVisible.value = false;
                mergedDialogMode.value = 'add';
                mergedDialogEditIndex.value = -1;
            } catch (error) {
                ElMessage.error('Save failed: ' + error.message);
            } finally {
                configSaving.value = false;
            }
        };

        // ========== End merged mode helpers ==========

        const saveConfigForService = async (service) => {
            const content = configContents[service];

            if (!content.trim()) {
                ElMessage.warning('Configuration content cannot be empty');
                return;
            }

            // Validate JSON format
            try {
                JSON.parse(content);
            } catch (e) {
                ElMessage.error('Invalid JSON format: ' + e.message);
                return;
            }

            configSaving.value = true;
            try {
                const result = await fetchWithErrorHandling(`/api/config/${service}`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ content })
                });

                if (result.success) {
                    ElMessage.success(result.message || 'Configuration saved successfully');
                    await loadData();
                } else {
                    ElMessage.error('Save failed: ' + (result.error || 'Unknown error'));
                }
            } catch (error) {
                ElMessage.error('Save failed: ' + error.message);
            } finally {
                configSaving.value = false;
            }
        };

        const saveConfig = async () => {
            const service = activeConfigTab.value;
            await saveConfigForService(service);
        };
        
        // Filter drawer helpers
        const openFilterDrawer = async () => {
            filterDrawerVisible.value = true;
            await loadFilter();
        };
        
        // Add a filter rule
        const addFilterRule = () => {
            filterRules.value.push({
                source: '',
                target: '',
                op: 'replace'
            });
        };
        
        // Remove a filter rule
        const removeFilterRule = (index) => {
            filterRules.value.splice(index, 1);
        };
        
        const closeFilterDrawer = () => {
            filterDrawerVisible.value = false;
        };
        
        const loadFilter = async () => {
            try {
                const data = await fetchWithErrorHandling('/api/filter');
                filterContent.value = data.content || '[]';
                
                // Parse JSON and map into rule objects
                try {
                    let parsedRules = JSON.parse(filterContent.value);
                    if (!Array.isArray(parsedRules)) {
                        parsedRules = [parsedRules];
                    }
                    filterRules.value = parsedRules.map(rule => ({
                        source: rule.source || '',
                        target: rule.target || '',
                        op: rule.op || 'replace'
                    }));
                } catch (e) {
                    // On parse failure, reset to empty
                    filterRules.value = [];
                }
            } catch (error) {
                filterRules.value = [];
                ElMessage.error('Failed to load filter rules: ' + error.message);
            }
        };
        
        const saveFilter = async () => {
            // Remove empty rules
            const validRules = filterRules.value.filter(rule => rule.source && rule.source.trim());
            
            if (validRules.length === 0) {
                const emptyRules = '[]';
                filterContent.value = emptyRules;
            } else {
                // Validate each rule
                for (const rule of validRules) {
                    if (!['replace', 'remove'].includes(rule.op)) {
                        ElMessage.error('op must be either replace or remove');
                        return;
                    }
                    if (rule.op === 'replace' && !rule.target) {
                        ElMessage.error('replace operations require a replacement value');
                        return;
                    }
                }
                
                // Convert back to JSON
                const jsonRules = validRules.map(rule => {
                    const obj = {
                        source: rule.source,
                        op: rule.op
                    };
                    if (rule.op === 'replace') {
                        obj.target = rule.target || '';
                    }
                    return obj;
                });
                
                filterContent.value = JSON.stringify(jsonRules, null, 2);
            }
            
            filterSaving.value = true;
            try {
                const result = await fetchWithErrorHandling('/api/filter', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ content: filterContent.value })
                });
                
                if (result.success) {
                    ElMessage.success(result.message || 'Filter rules saved successfully');
                    await loadData();
                } else {
                    ElMessage.error('Save failed: ' + (result.error || 'Unknown error'));
                }
            } catch (error) {
                ElMessage.error('Save failed: ' + error.message);
            } finally {
                filterSaving.value = false;
            }
        };
        
        // Utility helpers
        const formatTimestamp = (timestamp) => {
            if (!timestamp) return '-';

            try {
                const date = new Date(timestamp);
                const year = date.getFullYear();
                const month = String(date.getMonth() + 1).padStart(2, '0');
                const day = String(date.getDate()).padStart(2, '0');
                const hours = String(date.getHours()).padStart(2, '0');
                const minutes = String(date.getMinutes()).padStart(2, '0');
                const seconds = String(date.getSeconds()).padStart(2, '0');

                return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`;
            } catch (e) {
                return timestamp;
            }
        };
        
        const truncatePath = (path) => {
            // Show full URLs without truncation
            return path || '-';
        };
        
        const getStatusTagType = (statusCode) => {
            if (!statusCode) return '';
            
            const status = parseInt(statusCode);
            if (status >= 200 && status < 300) return 'success';
            if (status >= 400 && status < 500) return 'warning';
            if (status >= 500) return 'danger';
            return '';
        };
        
        // Log detail helpers
        const decodeBodyContent = (encodedContent) => {
            if (!encodedContent) {
                return '';
            }

            try {
                const decodedBytes = atob(encodedContent);
                const decodedText = decodeURIComponent(decodedBytes.split('').map(c =>
                    '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2)
                ).join(''));

                try {
                    const jsonObj = JSON.parse(decodedText);
                    return JSON.stringify(jsonObj, null, 2);
                } catch {
                    return decodedText;
                }
            } catch (error) {
                console.error('Failed to decode request body:', error);
                return 'Decode failed: ' + error.message;
            }
        };

        const PARSED_EMPTY_TEXT = 'Reasoning: none\nResponse: none\nTool calls: none';

        const parseResponseLogContent = (rawText, serviceName = '') => {
            const normalizeNewlines = (text) => {
                if (typeof text !== 'string') {
                    return '';
                }
                return text.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
            };

            const sanitize = (text) => {
                if (!text) {
                    return '';
                }
                const normalized = normalizeNewlines(String(text));
                return normalized
                    .split('\n')
                    .map(line => line.trimEnd())
                    .join('\n')
                    .trim();
            };

            const summarize = (value) => sanitize(value);

            const formatReasoningSummary = (value) => {
                if (!value || typeof value !== 'string') {
                    return '';
                }
                const cleaned = value.replace(/\*\*/g, '').trim();
                if (!cleaned) {
                    return '';
                }
                const parts = cleaned.split(/\n{2,}/);
                if (parts.length > 1) {
                    const rest = parts.slice(1).join('\n\n');
                    return `${sanitize(parts[0])}\n${sanitize(rest)}`.trim();
                }
                return sanitize(cleaned);
            };

            const buildOutput = (entries, errors) => {
                if (!entries.length) {
                    return {
                        success: true,
                        partial: errors.length > 0,
                        text: PARSED_EMPTY_TEXT,
                        errors
                    };
                }

                const labelMap = {
                    reasoning: 'Reasoning',
                    message: 'Response',
                    tool: 'Tool call'
                };

                const totals = entries.reduce((acc, entry) => {
                    acc[entry.kind] = (acc[entry.kind] || 0) + 1;
                    return acc;
                }, {});

                const seen = {};
                const outputLines = entries.map(entry => {
                    seen[entry.kind] = (seen[entry.kind] || 0) + 1;
                    const suffix = totals[entry.kind] > 1 ? `(${seen[entry.kind]})` : '';
                    if (entry.text && entry.text.includes('\n')) {
                        return `${labelMap[entry.kind]}${suffix}:\n${entry.text}`;
                    }
                    return `${labelMap[entry.kind]}${suffix}: ${entry.text ?? ''}`;
                });

                ['reasoning', 'message', 'tool'].forEach(kind => {
                    if (!totals[kind]) {
                        outputLines.push(`${labelMap[kind]}: none`);
                    }
                });

                return {
                    success: true,
                    partial: errors.length > 0,
                    text: outputLines.join('\n'),
                    errors
                };
            };

            const parseSseEvents = (text) => {
                const rawLines = text.split(/\r?\n/);
                const events = [];
                let currentEventName = null;
                let dataLines = [];

                const pushEvent = () => {
                    if (currentEventName && dataLines.length > 0) {
                        events.push({
                            event: currentEventName,
                            data: dataLines.join('\n')
                        });
                    }
                    dataLines = [];
                };

                for (const line of rawLines) {
                    if (line.startsWith('event:')) {
                        pushEvent();
                        currentEventName = line.slice(6).trim();
                    } else if (line.startsWith('data:')) {
                        dataLines.push(line.slice(5).trimStart());
                    } else if (line.trim() === '') {
                        pushEvent();
                        currentEventName = null;
                    } else if (dataLines.length > 0) {
                        dataLines.push(line);
                    }
                }
                pushEvent();

                return events;
            };

            const parseCodexEvents = (events) => {
                const entries = [];
                const errors = [];

                for (const evt of events) {
                    if (evt.event !== 'response.output_item.done' || !evt.data) {
                        continue;
                    }

                    let payload;
                    try {
                        payload = JSON.parse(evt.data);
                    } catch (error) {
                        errors.push(`JSON parse failed: ${error.message}`);
                        continue;
                    }

                    const item = payload?.item;
                    if (!item) {
                        continue;
                    }

                    if (item.type === 'reasoning') {
                        let added = false;
                        if (Array.isArray(item.summary)) {
                            for (const summaryItem of item.summary) {
                                if (summaryItem && typeof summaryItem.text === 'string') {
                                    const formatted = formatReasoningSummary(summaryItem.text);
                                    if (formatted) {
                                        entries.push({ kind: 'reasoning', text: formatted });
                                        added = true;
                                    }
                                }
                            }
                        }
                        if (!added) {
                            if (item.encrypted_content) {
                                entries.push({ kind: 'reasoning', text: 'Content encrypted; unable to parse' });
                            } else {
                                entries.push({ kind: 'reasoning', text: 'No summary provided' });
                            }
                        }
                    } else if (item.type === 'message') {
                        const parts = [];
                        if (Array.isArray(item.content)) {
                            for (const fragment of item.content) {
                                if (fragment && typeof fragment.text === 'string') {
                                    parts.push(fragment.text);
                                } else if (fragment?.delta && typeof fragment.delta.text === 'string') {
                                    parts.push(fragment.delta.text);
                                }
                            }
                        }
                        const combined = sanitize(parts.join(' '));
                        if (combined) {
                            entries.push({ kind: 'message', text: combined });
                        }
                    } else if (item.type === 'function_call') {
                        let description = item.name || 'function_call';
                        let argsObject = null;

                        if (typeof item.arguments === 'string') {
                            try {
                                argsObject = JSON.parse(item.arguments);
                            } catch (error) {
                                // Leave raw string for display
                            }
                        } else if (item.arguments && typeof item.arguments === 'object') {
                            argsObject = item.arguments;
                        }

                        const segments = [];
                        if (argsObject) {
                            if (Array.isArray(argsObject.command)) {
                                segments.push(`command=${summarize(argsObject.command.join(' '))}`);
                            } else if (argsObject.command) {
                                segments.push(`command=${summarize(argsObject.command)}`);
                            }
                            if (argsObject.workdir) {
                                segments.push(`workdir=${summarize(argsObject.workdir)}`);
                            }
                            if (argsObject.with_escalated_permissions !== undefined) {
                                segments.push(`escalated=${argsObject.with_escalated_permissions}`);
                            }
                            if (argsObject.timeout_ms !== undefined) {
                                segments.push(`timeout=${argsObject.timeout_ms}`);
                            }
                            if (argsObject.justification) {
                                segments.push(`justification=${summarize(argsObject.justification)}`);
                            }

                            const knownKeys = new Set(['command', 'workdir', 'with_escalated_permissions', 'timeout_ms', 'justification']);
                            for (const key of Object.keys(argsObject)) {
                                if (knownKeys.has(key)) continue;
                                const value = argsObject[key];
                                segments.push(`${key}=${typeof value === 'string' ? summarize(value) : summarize(JSON.stringify(value))}`);
                            }
                        } else if (item.arguments) {
                            segments.push(summarize(typeof item.arguments === 'string'
                                ? item.arguments
                                : JSON.stringify(item.arguments)));
                        }

                        if (segments.length > 0) {
                            description += ` | ${segments.join(' | ')}`;
                        }
                        entries.push({ kind: 'tool', text: description });
                    }
                }

                return buildOutput(entries, errors);
            };

            const parseClaudeEvents = (events) => {
                const entries = [];
                const errors = [];
                const activeBlocks = new Map();

                const finalizeBlock = (indexKey, block) => {
                    if (block.type === 'thinking') {
                        const normalized = normalizeNewlines(block.text || '');
                        if (normalized) {
                            entries.push({ kind: 'reasoning', text: normalized });
                        }
                        return;
                    }

                    if (block.type === 'text') {
                        const normalized = normalizeNewlines(block.text || '');
                        if (normalized) {
                            entries.push({ kind: 'message', text: normalized });
                        }
                        return;
                    }

                    if (block.type === 'tool_use') {
                        const toolLines = [];
                        if (block.toolName) {
                            toolLines.push(`Name: ${normalizeNewlines(block.toolName)}`);
                        }

                        let toolArgs = null;
                        if (block.partialJson) {
                            try {
                                toolArgs = JSON.parse(block.partialJson);
                            } catch (error) {
                                errors.push(`Tool call JSON parse failed (index=${indexKey}): ${error.message}`);
                                if (block.partialJson.trim()) {
                                    toolLines.push(`Original input: ${normalizeNewlines(block.partialJson)}`);
                                }
                            }
                        }

                        if (!toolArgs && block.inputSnapshot && typeof block.inputSnapshot === 'object' && Object.keys(block.inputSnapshot).length > 0) {
                            toolArgs = block.inputSnapshot;
                        }

                        const formatValue = (value) => {
                            if (value === null || value === undefined) {
                                return '';
                            }
                            if (typeof value === 'string') {
                                return normalizeNewlines(value);
                            }
                            if (typeof value === 'number' || typeof value === 'boolean') {
                                return String(value);
                            }
                            if (Array.isArray(value)) {
                                return normalizeNewlines(value.map(item => (
                                    typeof item === 'string' ? item : JSON.stringify(item)
                                )).join(' '));
                            }
                            try {
                                return normalizeNewlines(JSON.stringify(value));
                            } catch (error) {
                                return normalizeNewlines(String(value));
                            }
                        };

                        if (toolArgs && typeof toolArgs === 'object') {
                            if (Object.prototype.hasOwnProperty.call(toolArgs, 'command')) {
                                const commandValue = Array.isArray(toolArgs.command)
                                    ? toolArgs.command.join(' ')
                                    : toolArgs.command;
                                const formattedCommand = formatValue(commandValue);
                                if (formattedCommand) {
                                    toolLines.push(`Command: ${formattedCommand}`);
                                }
                            }

                            for (const key of Object.keys(toolArgs)) {
                                if (key === 'command') {
                                    continue;
                                }
                                const formatted = formatValue(toolArgs[key]);
                                if (formatted) {
                                    toolLines.push(`${key}：${formatted}`);
                                }
                            }
                        }

                        if (toolLines.length) {
                            entries.push({ kind: 'tool', text: toolLines.join('\n') });
                        }
                        return;
                    }
                };

                for (const evt of events) {
                    if (!evt.event || !evt.data) {
                        continue;
                    }

                    let payload;
                    try {
                        payload = JSON.parse(evt.data);
                    } catch (error) {
                        errors.push(`JSON parse failed: ${error.message}`);
                        continue;
                    }

                    if (evt.event === 'content_block_start') {
                        const indexKey = String(payload?.index ?? '');
                        const blockInfo = payload?.content_block || {};
                        if (!indexKey || !blockInfo?.type) {
                            continue;
                        }
                        activeBlocks.set(indexKey, {
                            type: blockInfo.type,
                            text: '',
                            partialJson: '',
                            toolName: blockInfo.name || '',
                            inputSnapshot: blockInfo.input,
                        });
                    } else if (evt.event === 'content_block_delta') {
                        const indexKey = String(payload?.index ?? '');
                        const block = activeBlocks.get(indexKey);
                        if (!block) {
                            continue;
                        }
                        const delta = payload?.delta;
                        if (!delta) {
                            continue;
                        }

                        if (delta.type === 'thinking_delta' && typeof delta.thinking === 'string') {
                            block.text += delta.thinking;
                        } else if (delta.type === 'text_delta' && typeof delta.text === 'string') {
                            block.text += delta.text;
                        } else if (delta.type === 'input_json_delta' && typeof delta.partial_json === 'string') {
                            block.partialJson += delta.partial_json;
                        }
                    } else if (evt.event === 'content_block_stop') {
                        const indexKey = String(payload?.index ?? '');
                        const block = activeBlocks.get(indexKey);
                        if (!block) {
                            continue;
                        }
                        finalizeBlock(indexKey, block);
                        activeBlocks.delete(indexKey);
                    }
                }

                if (activeBlocks.size > 0) {
                    for (const [indexKey, block] of activeBlocks.entries()) {
                        errors.push(`Content block did not terminate correctly (index=${indexKey})`);
                        finalizeBlock(indexKey, block);
                    }
                    activeBlocks.clear();
                }

                return buildOutput(entries, errors);
            };

            if (!rawText || !rawText.trim()) {
                return {
                    success: true,
                    partial: false,
                    text: PARSED_EMPTY_TEXT,
                    errors: []
                };
            }

            try {
                const events = parseSseEvents(rawText);
                const lowerService = (serviceName || '').toLowerCase();
                const hasClaudeBlocks = events.some(evt => evt.event && evt.event.startsWith('content_block'));

                if (lowerService === 'claude' || hasClaudeBlocks) {
                    return parseClaudeEvents(events);
                }

                return parseCodexEvents(events);
            } catch (error) {
                return {
                    success: false,
                    partial: false,
                    text: `Parsing failed: ${error.message}`,
                    errors: [error.message]
                };
            }
        };

        const showLogDetail = (log) => {
            selectedLog.value = log;
            activeLogTab.value = 'basic'; // Reset to basic information tab
            logDetailVisible.value = true;

            decodedRequestBody.value = decodeBodyContent(log.filtered_body);
            decodedOriginalRequestBody.value = decodeBodyContent(log.original_body);
            responseOriginalContent.value = decodeBodyContent(log.response_content);
            const serviceName = (log?.service || '').toLowerCase();
            isCodexLog.value = serviceName === 'codex';
            isClaudeLog.value = serviceName === 'claude';

            const shouldAttemptParse = (isCodexLog.value || isClaudeLog.value) && !!responseOriginalContent.value;

            if (shouldAttemptParse) {
                const parsedResult = parseResponseLogContent(responseOriginalContent.value, log?.service);
                if (parsedResult.success) {
                    parsedResponseContent.value = parsedResult.text;
                    decodedResponseContent.value = parsedResponseContent.value;
                    isResponseContentParsed.value = true;
                    if (parsedResult.partial) {
                        ElMessage.warning('Some response segments failed to parse; displaying available portions');
                        console.warn('Response parsing partially failed:', parsedResult.errors);
                    }
                } else {
                    parsedResponseContent.value = '';
                    decodedResponseContent.value = responseOriginalContent.value;
                    isResponseContentParsed.value = false;
                    ElMessage.warning(parsedResult.text || 'Response parsing failed');
                }
            } else {
                parsedResponseContent.value = '';
                decodedResponseContent.value = responseOriginalContent.value || '';
                isResponseContentParsed.value = false;
            }
        };

        const toggleParsedResponse = () => {
            if (!canParseSelectedLog.value) {
                return;
            }

            if (isResponseContentParsed.value) {
                isResponseContentParsed.value = false;
                decodedResponseContent.value = responseOriginalContent.value;
            } else {
                if (!parsedResponseContent.value) {
                    const parsedResult = parseResponseLogContent(responseOriginalContent.value, selectedLog.value?.service);
                    if (parsedResult.success) {
                        parsedResponseContent.value = parsedResult.text;
                        if (parsedResult.partial) {
                            ElMessage.warning('Some response segments failed to parse; displaying available portions');
                            console.warn('Response parsing partially failed:', parsedResult.errors);
                        }
                    } else {
                        ElMessage.warning(parsedResult.text || 'Response parsing failed');
                        return;
                    }
                }

                isResponseContentParsed.value = true;
                decodedResponseContent.value = parsedResponseContent.value || PARSED_EMPTY_TEXT;
            }
        };
        
        // Load every log entry
        const loadAllLogs = async () => {
            allLogsLoading.value = true;
            try {
                const data = await fetchWithErrorHandling('/api/logs/all');
                const logs = Array.isArray(data) ? data : [];
                // Backend already trimmed using logLimit; use payload directly
                allLogs.value = logs;
            } catch (error) {
                ElMessage.error('Failed to fetch all logs: ' + error.message);
                allLogs.value = [];
            } finally {
                allLogsLoading.value = false;
            }
        };

        // Show all logs
        const viewAllLogs = async () => {
            allLogsVisible.value = true;
            await loadAllLogs();
        };

        // Refresh all logs
        const refreshAllLogs = () => {
            loadAllLogs();
        };

        // Load system configuration
        const loadSystemConfig = async () => {
            try {
                const data = await fetchWithErrorHandling('/api/system/config');
                if (data.config) {
                    systemConfig.logLimit = data.config.logLimit || 50;
                }
            } catch (error) {
                console.error('Failed to load system configuration:', error);
                systemConfig.logLimit = 50;
            }
        };

        // Handle log limit changes
        const handleLogLimitChange = async (newLimit) => {
            try {
                allLogsLoading.value = true;

                // Persist configuration; backend trims log files automatically
                const response = await fetch('/api/system/config', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ logLimit: newLimit })
                });

                const result = await response.json();

                if (!response.ok) {
                    throw new Error(result.error || 'Failed to save configuration');
                }

                ElMessage.success(`Log limit set to ${newLimit}`);

                // Reload logs with new limit
                await loadAllLogs();

            } catch (error) {
                ElMessage.error('Failed to save configuration: ' + error.message);
            } finally {
                allLogsLoading.value = false;
            }
        };
        
        // Format JSON bodies
        const formatJsonContent = (bodyRef) => {
            if (!bodyRef.value) {
                ElMessage.warning('No request body content available');
                return;
            }

            try {
                const jsonObj = JSON.parse(bodyRef.value);
                bodyRef.value = JSON.stringify(jsonObj, null, 2);
                ElMessage.success('JSON formatted successfully');
            } catch (error) {
                ElMessage.error('Invalid JSON format');
            }
        };

        const formatFilteredRequestBody = () => formatJsonContent(decodedRequestBody);
        const formatOriginalRequestBody = () => formatJsonContent(decodedOriginalRequestBody);
        const formatResponseContent = () => formatJsonContent(decodedResponseContent);
        
        
        // Sort response headers alphabetically
        const getSortedHeaderKeys = (headers) => {
            if (!headers || typeof headers !== 'object') {
                return [];
            }
            return Object.keys(headers).sort((a, b) => {
                // Case-insensitive alphabetical ordering
                return a.toLowerCase().localeCompare(b.toLowerCase());
            });
        };

        // Copy helper
        const copyToClipboard = async (text) => {
            try {
                await navigator.clipboard.writeText(text);
                ElMessage.success('Copied to clipboard');
            } catch (error) {
                // Fallback for environments without clipboard API
                const textArea = document.createElement('textarea');
                textArea.value = text;
                textArea.style.position = 'fixed';
                textArea.style.opacity = '0';
                document.body.appendChild(textArea);
                textArea.select();
                try {
                    document.execCommand('copy');
                    ElMessage.success('Copied to clipboard');
                } catch (err) {
                    ElMessage.error('Copy failed');
                }
                document.body.removeChild(textArea);
            }
        };
        
        // Clear all logs
        const clearAllLogs = async () => {
            try {
                await ElMessageBox.confirm(
                    'Clear all logs? This action cannot be undone.',
                    'Confirm purge',
                    {
                        confirmButtonText: 'Confirm',
                        cancelButtonText: 'Cancel',
                        type: 'warning',
                    }
                );
                
                const result = await fetchWithErrorHandling('/api/logs', {
                    method: 'DELETE'
                });
                
                if (result.success) {
                    ElMessage.success('Logs cleared');
                    // Refresh page data
                    window.location.reload();
                } else {
                    ElMessage.error('Failed to clear logs: ' + (result.error || 'Unknown error'));
                }
            } catch (error) {
                if (error !== 'cancel') {
                    ElMessage.error('Failed to clear logs: ' + error.message);
                }
            }
        };
        
        // Watchers to keep JSON and form state in sync
        // Watch Claude configuration JSON
        watch(() => configContents.claude, (newValue) => {
            // Delay execution to avoid feedback loops
            nextTick(() => {
                syncJsonToForm('claude');
                // Update merged view when JSON changes
                if (configEditMode.value === 'merged') {
                    buildMergedFromFriendly('claude');
                }
            });
        });

        // Watch Legacy configuration JSON
        watch(() => configContents.legacy, (newValue) => {
            nextTick(() => {
                syncJsonToForm('legacy');
                if (configEditMode.value === 'merged') {
                    buildMergedFromFriendly('legacy');
                }
            });
        });

        // Watch Codex configuration JSON
        watch(() => configContents.codex, (newValue) => {
            // Delay execution to avoid feedback loops
            nextTick(() => {
                syncJsonToForm('codex');
                // Update merged view when JSON changes
                if (configEditMode.value === 'merged') {
                    buildMergedFromFriendly('codex');
                }
            });
        });

        // Watch configuration edit mode changes
        watch(() => configEditMode.value, (newMode, oldMode) => {
            if (newMode === 'merged') {
                // Enter merged mode by rebuilding from friendlyConfigs
                buildMergedFromFriendly(activeConfigTab.value);
            }
        });

        // Watch active configuration tab (merged mode)
        watch(() => activeConfigTab.value, (newTab) => {
            if (configEditMode.value === 'merged') {
                buildMergedFromFriendly(newTab);
            }
        });

        // Real-time request helpers
        const initRealTimeConnections = () => {
            try {
                realtimeManager.value = new RealTimeManager();
                const removeListener = realtimeManager.value.addListener(handleRealTimeEvent);

                // Cleanup when page unloads
                window.addEventListener('beforeunload', () => {
                    removeListener();
                    if (realtimeManager.value) {
                        realtimeManager.value.destroy();
                    }
                });

                // Disable auto-connect; require manual connect
                // setTimeout(() => {
                //     realtimeManager.value.connectAll();
                //     console.log('Real-time manager initialized');
                // }, 1000); // Connect after 1 second delay
                console.log('Real-time manager initialized. Click reconnect to establish connections.');
            } catch (error) {
                console.error('Failed to initialize real-time connections:', error);
                ElMessage.error('Failed to initialize real-time connections: ' + error.message);
            }
        };

        const handleRealTimeEvent = (event) => {
            try {
                switch (event.type) {
                    case 'connection':
                        connectionStatus[event.service] = event.status === 'connected';
                        if (event.status === 'connected') {
                            console.log(`${event.service} real-time connection established`);
                        } else if (event.status === 'disconnected') {
                            console.log(`${event.service} real-time connection closed`);
                        } else if (event.status === 'error') {
                            console.log(`${event.service} real-time connection error:`, event.error);
                        }
                        break;

                    case 'snapshot':
                    case 'started':
                        addRealtimeRequest(event);
                        break;

                    case 'progress':
                        updateRequestProgress(event);
                        break;

                    case 'completed':
                    case 'failed':
                        completeRequest(event);
                        break;

                    default:
                }
            } catch (error) {
                console.error('Failed to process real-time event:', error);
            }
        };

        const addRealtimeRequest = (event) => {
            try {
                const existingIndex = realtimeRequests.value.findIndex(r => r.request_id === event.request_id);

                if (existingIndex >= 0) {
                    // Update existing request
                    Object.assign(realtimeRequests.value[existingIndex], event);
                } else {
                    // Add new request entry
                    const request = {
                        ...event,
                        responseText: '',
                        displayDuration: event.duration_ms || 0
                    };

                    realtimeRequests.value.unshift(request);

                    // Trim list to configured maximum
                    if (realtimeRequests.value.length > maxRealtimeRequests) {
                        realtimeRequests.value = realtimeRequests.value.slice(0, maxRealtimeRequests);
                    }
                }
            } catch (error) {
                console.error('Failed to add real-time request:', error);
            }
        };

        const updateRequestProgress = (event) => {
            try {
                const request = realtimeRequests.value.find(r => r.request_id === event.request_id);
                if (!request) return;

                // Update status and duration
                if (event.status) {
                    request.status = event.status;
                }
                if (event.duration_ms !== undefined) {
                    request.displayDuration = event.duration_ms;
                }

                // Append response delta to text
                if (event.response_delta) {
                    request.responseText += event.response_delta;

                    // Auto-scroll when detail drawer is open on this request
                    if (realtimeDetailVisible.value &&
                        selectedRealtimeRequest.value?.request_id === event.request_id) {
                        nextTick(() => {
                            scrollResponseToBottom();
                        });
                    }
                }
            } catch (error) {
                console.error('Failed to update request progress:', error);
            }
        };

        const completeRequest = (event) => {
            try {
                const request = realtimeRequests.value.find(r => r.request_id === event.request_id);
                if (!request) return;

                request.status = event.status || (event.type === 'completed' ? 'COMPLETED' : 'FAILED');
                request.displayDuration = event.duration_ms || request.displayDuration;
                request.status_code = event.status_code;

                if (isLoadbalanceWeightMode.value) {
                    loadLoadbalanceConfig().catch(err => console.error('Failed to refresh load-balancing data:', err));
                }
            } catch (error) {
                console.error('Failed to finalize request:', error);
            }
        };

        // UI helper utilities
        const formatRealtimeTime = (isoString) => {
            try {
                return new Date(isoString).toLocaleTimeString(undefined);
            } catch (error) {
                return isoString;
            }
        };

        const getStatusDisplay = (status) => {
            return REQUEST_STATUS[status] || { text: status, type: '' };
        };

        const showRealtimeDetail = (request) => {
            selectedRealtimeRequest.value = request;
            realtimeDetailVisible.value = true;
        };

        const scrollResponseToBottom = () => {
            try {
                const container = document.querySelector('.response-stream-content');
                if (container) {
                    container.scrollTop = container.scrollHeight;
                }
            } catch (error) {
                console.error('Failed to scroll response content:', error);
            }
        };

        const reconnectRealtime = () => {
            if (realtimeManager.value) {
                console.log('Reconnecting real-time services manually...');
                console.log('Current connection status:', realtimeManager.value.getConnectionStatus());
                console.log('Manager status:', realtimeManager.value.getStatus());

                realtimeManager.value.reconnectAll();
                ElMessage.info('Reconnecting real-time services...');
            }
        };

        const disconnectRealtime = () => {
            if (realtimeManager.value) {
                console.log('Disconnecting real-time services manually...');
                realtimeManager.value.disconnectAll();
                ElMessage.info('Real-time services disconnected');
            }
        };

        // Debug helper
        const checkConnectionStatus = () => {
            if (realtimeManager.value) {
                console.log('=== Connection status debug ===');
                console.log('Connection status:', realtimeManager.value.getConnectionStatus());
                console.log('Manager status:', realtimeManager.value.getStatus());
                console.log('Current realtime request count:', realtimeRequests.value.length);
                console.log('Realtime requests:', realtimeRequests.value);
                console.log('========================');
            }
        };

        // Expose helper to global scope
        window.debugRealtime = checkConnectionStatus;

        // Component mount hook
        onMounted(async () => {
            updateViewport();
            if (typeof window !== 'undefined') {
                window.addEventListener('resize', updateViewport, { passive: true });
            }

            await loadSystemConfig();
            loadData();
            // Initialize real-time connections
            initRealTimeConnections();
        });

        onBeforeUnmount(() => {
            if (typeof window !== 'undefined') {
                window.removeEventListener('resize', updateViewport);
            }
        });
        
        return {
            // Reactive state
            loading,
            logsLoading,
            allLogsLoading,
            configSaving,
            filterSaving,
            lastUpdate,
            services,
            stats,
            logs,
            allLogs,
            claudeConfigs,
            legacyConfigs,
            codexConfigs,
            configDrawerVisible,
            filterDrawerVisible,
            logDetailVisible,
            allLogsVisible,
            drawerSizes,
            dialogSizes,
            isMobileViewport,
            activeConfigTab,
            activeLogTab,
            showTransformedData,
            activeRequestSubTab,
            activeResponseSubTab,
            configContents,
            filterContent,
            filterRules,
            selectedLog,
            decodedRequestBody,
            decodedOriginalRequestBody,
            usageSummary,
            usageDrawerVisible,
            usageDetails,
            usageDetailsLoading,
            usageMetricLabels,
            metricKeys,
            friendlyConfigs,
            configEditMode,
            editingNewSite,
            newSiteData,
            // Merged mode state
            mergedConfigs,
            mergedDialogVisible,
            mergedDialogService,
            mergedDialogDraft,
            mergedDialogMode,
            modelSelectorVisible,
            testResultVisible,
            testingConnection,
            testConfig,
            lastTestResult,
            newSiteTestResult,
            testResponseDialogVisible,
            testResponseData,

            // General actions
            refreshData,
            switchConfig,
            openConfigDrawer,
            closeConfigDrawer,
            saveConfig,
            openFilterDrawer,
            closeFilterDrawer,
            loadFilter,
            saveFilter,
            addFilterRule,
            removeFilterRule,
            formatTimestamp,
            truncatePath,
            getStatusTagType,
            showLogDetail,
            viewAllLogs,
            refreshAllLogs,
            clearAllLogs,
            systemConfig,
            handleLogLimitChange,
            copyToClipboard,
            formatFilteredRequestBody,
            formatOriginalRequestBody,
            formatResponseContent,
            responseOriginalContent,
            isResponseContentParsed,
            toggleParsedResponse,
            isCodexLog,
            isClaudeLog,
            canParseSelectedLog,
            decodedResponseContent,
            formatUsageValue,
            formatUsageSummary,
            getUsageFormattedValue,
            modelSettingsVisible,
            effortByModel,
            verbosityByModel,
            summaryByModel,
            formatChannelName,
            formatServiceWithChannel,
            formatMethodWithURL,
            openUsageDrawer,
            closeUsageDrawer,
            clearUsageData,
            loadUsageDetails,
            getSortedHeaderKeys,
            startAddingSite,
            confirmAddSite,
            cancelAddSite,
            saveInteractiveConfig,
            removeConfigSite,
            handleActiveChange,
            syncFormToJson,
            syncJsonToForm,
            openModelSettings,
            saveModelSettings,
            // Merged mode helpers
            isValidUrl,
            buildMergedFromFriendly,
            rebuildFriendlyFromMerged,
            applyMergedGroupUpdate,
            autoGenerateName,
            enforceSingleActive,
            toggleMergedActive,
            updateMergedEntryAuth,
            applyMergedEntryNameUpdate,
            addMergedEntry,
            removeMergedEntry,
            deleteMergedGroup,
            switchToMergedMode,
            saveMergedConfig,
            openMergedDialog,
            editMergedGroup,
            isMergedGroupActive,
            addDialogEntry,
            removeDialogEntry,
            cancelMergedDialog,
            submitMergedDialog,
            getModelOptions,
            testNewSiteConnection,
            testSiteConnection,
            showModelSelector,
            cancelModelSelection,
            confirmModelSelection,
            copyTestResult,
            showTestResponse,
            testResponseDialogVisible,
            testResponseData,
            copyTestResponseData,

            // Real-time request exports
            realtimeRequests,
            realtimeDetailVisible,
            selectedRealtimeRequest,
            connectionStatus,
            formatRealtimeTime,
            getStatusDisplay,
            showRealtimeDetail,
            reconnectRealtime,
            disconnectRealtime,

            // Model routing exports
            routingMode,
            routingConfig,
            routingConfigSaving,
            modelMappingDrawerVisible,
            configMappingDrawerVisible,
            activeModelMappingTab,
            activeConfigMappingTab,
            selectRoutingMode,
            getRoutingModeText,
            openModelMappingDrawer,
            openConfigMappingDrawer,
            closeModelMappingDrawer,
            closeConfigMappingDrawer,
            addModelMapping,
            removeModelMapping,
            addConfigMapping,
            removeConfigMapping,
            saveRoutingConfig,
            loadRoutingConfig,

            // Load-balancing exports
            loadbalanceConfig,
            loadbalanceSaving,
            loadbalanceLoading,
            loadbalanceDisabledNotice,
            isLoadbalanceWeightMode,
            weightedTargets,
            selectLoadbalanceMode,
            pinWeightedTarget,
            resetLoadbalanceFailures,
            resettingFailures,
            pinningState
        };
    }
});

// Verify Element Plus is available
console.log('ElementPlus object:', ElementPlus);
console.log('ElementPlus.version:', ElementPlus.version);

// Register Element Plus components and directives
try {
    app.use(ElementPlus);
    console.log('Element Plus configured successfully');
} catch (error) {
    console.error('Element Plus configuration failed:', error);
}

// Mount the application
app.mount('#app');
