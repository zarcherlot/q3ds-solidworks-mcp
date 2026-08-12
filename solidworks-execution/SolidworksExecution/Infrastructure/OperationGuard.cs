using System.Collections.Concurrent;
using System.Threading;
using SolidworksExecution.Models;

namespace SolidworksExecution.Infrastructure
{
    public class OperationGuard : IOperationGuard
    {
        private static readonly OperationGuard _instance = new OperationGuard();
        public static OperationGuard Instance => _instance;

        private readonly ConcurrentDictionary<string, ExecutionResponse> _completed
            = new ConcurrentDictionary<string, ExecutionResponse>();
        private readonly ConcurrentQueue<string> _completionOrder = new ConcurrentQueue<string>();
        private const int MaxRememberedOperations = 10000;

        private volatile int _currentStateVersion = 0;

        private OperationGuard() { }

        public bool IsDuplicate(string operationId)
        {
            return _completed.ContainsKey(operationId);
        }

        public ExecutionResponse GetDuplicate(string operationId)
        {
            if (_completed.TryGetValue(operationId, out var original))
            {
                return new ExecutionResponse
                {
                    OperationId = operationId,
                    Status = "DUPLICATE",
                    Verified = original.Verified,
                    StateVersion = _currentStateVersion,
                    LastKnownStateVersion = _currentStateVersion,
                    CadState = null,
                    ResultGeometry = original.ResultGeometry,
                    Error = original.Error
                };
            }
            return null;
        }

        public bool IsStateVersionValid(int incomingStateVersion)
        {
            return incomingStateVersion == _currentStateVersion;
        }

        public void RegisterCompleted(string operationId, ExecutionResponse response)
        {
            _completed[operationId] = response;
            _completionOrder.Enqueue(operationId);
            while (_completed.Count > MaxRememberedOperations)
            {
                string expired;
                ExecutionResponse ignored;
                if (!_completionOrder.TryDequeue(out expired)) break;
                _completed.TryRemove(expired, out ignored);
            }
            Interlocked.Increment(ref _currentStateVersion);
        }

        public int GetCurrentStateVersion()
        {
            return _currentStateVersion;
        }
    }
}
