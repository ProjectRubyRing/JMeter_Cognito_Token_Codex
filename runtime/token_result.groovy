// JSON Extractor runs before this postprocessor. Scrubbing runs on every response.
import java.util.concurrent.ThreadLocalRandom
try {
    def token = vars.get('raw_access_token')
    def kind = vars.get('raw_token_type')
    def lifetimeText = vars.get('raw_expires_in')
    if (prev.getResponseCode() != '200' || !prev.isSuccessful() ||
        !token || token == '__MISSING__' || !(token ==~ /[A-Za-z0-9._~+\/=-]+/) ||
        !'Bearer'.equalsIgnoreCase(kind) || !(lifetimeText ==~ /[0-9]+/)) {
        throw new IllegalArgumentException('Invalid OAuth response')
    }
    long lifetime = Long.parseLong(lifetimeText)
    if (lifetime <= 0 || lifetime > 86400) throw new IllegalArgumentException('Invalid expires_in')
    double ratio = (Double) vars.getObject('__reuse_ratio')
    double jitter = (Double) vars.getObject('__jitter_fraction')
    double safety = Math.min((Double) vars.getObject('__safety_seconds'), lifetime * 0.1d)
    double useSeconds = Math.min(lifetime * ratio * (1.0d - ThreadLocalRandom.current().nextDouble() * jitter), lifetime - safety)
    long started = (Long) vars.getObject('__token_started_nanos')
    long deadline = started + (long) Math.floor(useSeconds * 1_000_000_000d)
    if (System.nanoTime() >= deadline) throw new IllegalArgumentException('Token response arrived too late')
    vars.put('access_token', token)
    vars.putObject('__renew_at_nanos', deadline)
} catch (Exception ignored) {
    vars.remove('access_token')
    vars.remove('__renew_at_nanos')
    prev.setSuccessful(false)
    prev.setStopThread(true)
    prev.setResponseMessage('OAuth acquisition failed; inspect HTTP status and private Cognito settings. Sensitive response redacted.')
} finally {
    prev.setResponseData('[REDACTED OAuth response]', 'UTF-8')
    prev.setSamplerData('')
    prev.setRequestHeaders('')
    prev.setResponseHeaders('')
    vars.remove('cognito_basic')
    vars.remove('raw_access_token')
    vars.remove('raw_expires_in')
    vars.remove('raw_token_type')
}
