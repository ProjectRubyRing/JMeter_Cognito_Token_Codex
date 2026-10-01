// Executes before EVERY selected API operation. vars belong to this JMeter thread.
import java.net.URI
import java.net.URLEncoder
import java.nio.charset.StandardCharsets
import java.util.Base64

try {
    if (!vars.get('__auth_initialized')) {
        def setting = { String env, String property, String fallback ->
            def value = System.getenv(env)
            value != null && !value.isEmpty() ? value : props.getProperty(property, fallback)
        }
        def uri = new URI(setting('COGNITO_TOKEN_URL', 'cognito_token_url', ''))
        boolean loopback = uri.host in ['localhost', '127.0.0.1', '[::1]', '::1']
        boolean mockHttp = props.getProperty('allow_http_token_loopback', 'false') == 'true' && loopback
        if (!uri.host || uri.userInfo || uri.query || uri.fragment ||
            !(uri.scheme == 'https' || uri.scheme == 'http' && mockHttp) ||
            !uri.rawPath || uri.host.endsWith('.invalid')) {
            throw new IllegalArgumentException('Set a valid HTTPS COGNITO_TOKEN_URL; HTTP is allowed only for explicit loopback mock tests')
        }
        def clientId = System.getenv('COGNITO_CLIENT_ID')
        def secret = System.getenv('COGNITO_CLIENT_SECRET')
        if (!clientId || !secret || clientId.contains(':') || clientId == 'YOUR_CLIENT_ID') {
            throw new IllegalArgumentException('Set COGNITO_CLIENT_ID and COGNITO_CLIENT_SECRET in the JMeter process environment')
        }
        def scope = setting('COGNITO_SCOPE', 'cognito_scope', '')
        if (!scope || scope =~ /[\r\n]/) {
            throw new IllegalArgumentException('Set space-separated custom COGNITO_SCOPE values')
        }
        double ratio = Double.parseDouble(props.getProperty('token_reuse_ratio', '0.75'))
        double jitter = Double.parseDouble(props.getProperty('token_jitter_fraction', '0.05'))
        double safety = Double.parseDouble(props.getProperty('token_safety_seconds', '30'))
        if (!Double.isFinite(ratio) || ratio <= 0 || ratio >= 1 ||
            !Double.isFinite(jitter) || jitter < 0 || jitter > 0.5 ||
            !Double.isFinite(safety) || safety < 0 || safety > 3600) {
            throw new IllegalArgumentException('Invalid token reuse ratio/jitter/safety settings')
        }
        def apiHost = vars.get('host')
        if (!apiHost || !(apiHost ==~ /[A-Za-z0-9.:\[\]-]+/) ||
            !(vars.get('protocol') in ['http','https']) ||
            !(Integer.parseInt(vars.get('port')) in 1..65535)) {
            throw new IllegalArgumentException('Invalid API host/protocol/port')
        }
        if (vars.get('protocol') == 'http' && !(apiHost in ['localhost','127.0.0.1','::1','[::1]']) &&
            props.getProperty('allow_http_api', 'false') != 'true') {
            throw new IllegalArgumentException('Use HTTPS for a remote API, or explicitly set allow_http_api=true in a controlled test network')
        }
        def apiKey = System.getenv('API_KEY') ?: vars.get('api_key')
        if (apiKey =~ /[\r\n]/) {
            throw new IllegalArgumentException('API_KEY contains forbidden line breaks')
        }
        vars.put('api_key', apiKey)
        vars.put('token_host', uri.host)
        vars.put('token_port', Integer.toString(uri.port < 0 ? (uri.scheme == 'https' ? 443 : 80) : uri.port))
        vars.put('token_protocol', uri.scheme)
        vars.put('token_path', uri.rawPath)
        vars.put('cognito_scope', scope)
        vars.putObject('__reuse_ratio', ratio)
        vars.putObject('__jitter_fraction', jitter)
        vars.putObject('__safety_seconds', safety)
        vars.put('__auth_initialized', 'true')
    }
    long pauseMs = Long.parseLong(props.getProperty('think_time_ms', '0'))
    if (pauseMs < 0 || pauseMs > 3600000) {
        throw new IllegalArgumentException('think_time_ms must be 0..3600000')
    }
    if (pauseMs > 0) Thread.sleep(pauseMs)
    Long deadline = (Long) vars.getObject('__renew_at_nanos')
    boolean needed = !vars.get('access_token') || deadline == null || System.nanoTime() >= deadline ||
        props.getProperty('token_each_request', 'false') == 'true'
    vars.put('token_needed', Boolean.toString(needed))
    if (needed) {
        vars.remove('access_token')
        vars.remove('raw_access_token')
        vars.remove('raw_expires_in')
        vars.remove('raw_token_type')
        def id = URLEncoder.encode(System.getenv('COGNITO_CLIENT_ID'), 'UTF-8')
        def secret = URLEncoder.encode(System.getenv('COGNITO_CLIENT_SECRET'), 'UTF-8')
        vars.put('cognito_basic', 'Basic ' + Base64.encoder.encodeToString((id + ':' + secret).getBytes(StandardCharsets.UTF_8)))
        vars.putObject('__token_started_nanos', System.nanoTime())
    }
    SampleResult.setIgnore()
} catch (InterruptedException ignored) {
    Thread.currentThread().interrupt()
    SampleResult.setSuccessful(false)
    SampleResult.setResponseCode('CANCELLED')
    SampleResult.setResponseMessage('Token preparation cancelled')
    SampleResult.setStopThread(true)
} catch (Exception ignored) {
    // Do not log exception text: malformed input may contain credential material.
    vars.remove('access_token')
    vars.remove('cognito_basic')
    SampleResult.setSuccessful(false)
    SampleResult.setResponseCode('CONFIG_ERROR')
    SampleResult.setResponseMessage('Check Cognito environment variables, HTTPS endpoint and runtime settings; see handbook')
    SampleResult.setResponseData('Configuration validation failed. No API request sent.', 'UTF-8')
    SampleResult.setStopThread(true)
}
