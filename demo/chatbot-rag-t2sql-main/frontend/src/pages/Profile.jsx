import { useAuth } from '../contexts/AuthContext';
import { useNavigate, Link } from 'react-router-dom';

export default function Profile() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const handleLogout = () => {
    logout();
    navigate('/login');
  };

  if (!user) {
    return null;
  }

  return (
    <div className="min-h-screen bg-[#F9F9F5] py-12 px-4 sm:px-6 lg:px-8">
      <div className="max-w-3xl mx-auto">
        {/* Back to Chat Button */}
        <div className="mb-6">
          <Link 
            to="/" 
            className="inline-flex items-center gap-2 text-[#5E507F] hover:text-[#4A3F71] transition-colors duration-200"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 19l-7-7m0 0l7-7m-7 7h18" />
            </svg>
            Back to Chat
          </Link>
        </div>

        {/* Profile Card */}
        <div className="bg-white rounded-xl shadow-lg border border-gray-100 overflow-hidden">
          {/* Header with gradient */}
          <div className="relative bg-gradient-to-r from-[#4A3F71] to-[#5E507F] px-8 py-8">
            <div className="absolute inset-0 bg-[url('data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iNjAiIGhlaWdodD0iNjAiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyI+PGRlZnM+PHBhdHRlcm4gaWQ9ImdyaWQiIHdpZHRoPSI2MCIgaGVpZ2h0PSI2MCIgcGF0dGVyblVuaXRzPSJ1c2VyU3BhY2VPblVzZSI+PHBhdGggZD0iTSAxMCAwIEwgMCAwIDAgMTAiIGZpbGw9Im5vbmUiIHN0cm9rZT0id2hpdGUiIHN0cm9rZS13aWR0aD0iMC41Ii8+PC9wYXR0ZXJuPjwvZGVmcz48cmVjdCB3aWR0aD0iMTAwJSIgaGVpZ2h0PSIxMDAlIiBmaWxsPSJ1cmwoI2dyaWQpIi8+PC9zdmc+')] opacity-5"></div>
            <div className="relative flex justify-between items-center">
              <div className="flex items-center gap-4">
                <div className="w-20 h-20 bg-white/20 rounded-full flex items-center justify-center text-4xl">
                  👤
                </div>
                <div>
                  <h3 className="text-2xl font-bold text-white">
                    {user.fullname}
                  </h3>
                  <p className="text-white/80 text-sm mt-1">
                    @{user.username}
                  </p>
                </div>
              </div>
              <button
                onClick={handleLogout}
                className="px-4 py-2 border border-white/30 text-sm font-medium rounded-lg text-white hover:bg-white/10 focus:outline-none focus:ring-2 focus:ring-white/50 transition-all duration-200"
              >
                Logout
              </button>
            </div>
          </div>

          {/* Profile Details */}
          <div className="px-8 py-6">
            <h4 className="text-lg font-semibold text-gray-800 mb-4">Personal Information</h4>
            <div className="space-y-4">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="bg-gray-50 rounded-lg p-4">
                  <dt className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-1">User ID</dt>
                  <dd className="text-sm text-gray-900 font-mono">
                    {user.user_id}
                  </dd>
                </div>
                <div className="bg-gray-50 rounded-lg p-4">
                  <dt className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-1">Username</dt>
                  <dd className="text-sm text-gray-900 font-medium">
                    {user.username}
                  </dd>
                </div>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="bg-gray-50 rounded-lg p-4">
                  <dt className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-1">Full Name</dt>
                  <dd className="text-sm text-gray-900 font-medium">
                    {user.fullname}
                  </dd>
                </div>
                <div className="bg-gray-50 rounded-lg p-4">
                  <dt className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-1">Gender</dt>
                  <dd className="text-sm text-gray-900">
                    {user.user_gender}
                  </dd>
                </div>
              </div>

              <div className="bg-gray-50 rounded-lg p-4">
                <dt className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-1">Date of Birth</dt>
                <dd className="text-sm text-gray-900">
                  {new Date(user.user_dob).toLocaleDateString('en-US', { 
                    year: 'numeric', 
                    month: 'long', 
                    day: 'numeric' 
                  })}
                </dd>
              </div>

              <div className="bg-gray-50 rounded-lg p-4">
                <dt className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-1">Address</dt>
                <dd className="text-sm text-gray-900">
                  {user.user_address}
                </dd>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
