import java.io.FileInputStream;
import java.nio.file.Path;
import java.util.concurrent.atomic.AtomicInteger;
import org.apache.jmeter.reporters.ResultCollector;
import org.apache.jmeter.reporters.ResultCollectorHelper;
import org.apache.jmeter.samplers.SampleResult;
import org.apache.jmeter.save.SaveService;
import org.apache.jmeter.util.JMeterUtils;
import org.apache.jmeter.visualizers.Visualizer;

/** Verify XML JTL through JMeter's actual listener loading path, without a GUI. */
public final class JtlLoadCheck {
    public static void main(String[] args) throws Exception {
        JMeterUtils.setJMeterHome(args[0]);
        JMeterUtils.loadJMeterProperties(Path.of(args[0], "bin", "jmeter.properties").toString());
        JMeterUtils.initLocale();
        SaveService.loadProperties();
        AtomicInteger count = new AtomicInteger();
        AtomicInteger unicode = new AtomicInteger();
        Visualizer visualizer = new Visualizer() {
            public void add(SampleResult result) {
                count.incrementAndGet();
                if (result.getResponseDataAsString().contains("\u78ba\u8a8d\u6e08\u307f")) {
                    unicode.incrementAndGet();
                }
            }
            public boolean isStats() { return false; }
        };
        ResultCollector collector = new ResultCollector();
        try (FileInputStream input = new FileInputStream(args[1])) {
            SaveService.loadTestResults(input, new ResultCollectorHelper(collector, visualizer));
        }
        if (count.get() != Integer.parseInt(args[2])) {
            throw new AssertionError("Unexpected sample count: " + count.get());
        }
        System.out.println("JMeter SaveService loaded " + count.get() + " samples through the listener loading API.");
    }
}
